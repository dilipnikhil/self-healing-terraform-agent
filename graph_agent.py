import os
import asyncio
import subprocess
import json
import textwrap
from typing import List
from typing_extensions import TypedDict, NotRequired
from langgraph.graph import StateGraph, END
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv
import requests

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover
    DDGS = None

# Load environment variables from .env file
load_dotenv()

# --- 1. CONFIGURATION ---
llm = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", "https://dfran-m6zqnnwy-eastus2.cognitiveservices.azure.com/"),
    api_version="2024-12-01-preview",
    deployment_name="gpt-4o-mini",
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    temperature=0.1
)

# --- 2. THE SPECIALIST PROMPTS ---

# Agent A: The "Docs" Researcher
RESEARCHER_PROMPT = """
You are the Terraform Knowledge Base.
Goal: Provide the EXACT Terraform AWS v5 syntax for the requested resources.
OUTPUT: A cheat sheet of valid resource blocks.
RULES:
1. Do NOT write the full code. Just the resource skeletons.
2. Emphasize SEPARATE resources for versioning, encryption, and public access blocks.
"""

# Agent B: The Security Officer (CISO)
SECURITY_PROMPT = """
You are the Chief Information Security Officer (CISO).
Goal: List the security constraints for the requested infrastructure.
OUTPUT: A bulleted list of requirements (e.g., "Must have SSE-KMS", "Must block public ACLs").
"""

# Agent C: The Architect
ARCHITECT_PROMPT = """
You are a Lead Cloud Architect.
Goal: Write the final executable Terraform code.
INPUTS:
1. Syntax Guide (from Researcher)
2. Security Policy (from CISO)

INSTRUCTIONS:
- Combine the Syntax Guide and Security Policy to write the code.
- Output ONLY the HCL code. No markdown.
"""

TRIAGE_PROMPT = """
You are the DevOps Site Reliability Engineer on-call.
You receive Terraform validation or Checkov security failures from downstream tools.
Analyze the failure and respond in strict JSON with the following keys:
- summary: short description of root cause.
- fix_instructions: concrete steps the architect should take to resolve the issue.
- needs_additional_research: true/false depending on whether we must gather more context (e.g., missing provider, variables, or policies).
- follow_up_prompt: extra context or clarifying questions the researcher/security agents should consider if more research is required. Can be empty.
- should_abort: true/false if the error is unrecoverable.
"""

DISCOVERY_PROMPT = """
You are a research assistant that reads authoritative documentation snippets.
Given terraform doc excerpts, summarize key configuration requirements and surface canonical resource names.
Return a concise bulleted list that the researcher and security agents can reference.
"""

# --- 3. DEFINE STATE ---
class AgentState(TypedDict):
    request: str         # The user's original goal
    messages: list       # Chat history
    syntax_guide: str    # Output from Researcher
    security_policy: str # Output from CISO
    code: str            # Final Terraform Code
    error: str           # Validator errors
    retry_count: int     # Loop counter
    status: str          # success/failed
    diagnosis: NotRequired[str]            # Latest analysis of failure
    fix_instructions: NotRequired[str]     # Targeted guidance for architect
    follow_up_prompt: NotRequired[str]     # Extra context for researcher/security
    next_node: NotRequired[str]            # Routing decision from triage
    documentation_urls: NotRequired[List[str]]
    documentation_snippets: NotRequired[str]

# --- 4. DEFINE NODES ---

async def research_agent(state: AgentState):
    """Async Agent: Fetches syntax rules."""
    print("   -> 🔎 [Async] Researcher is looking up Terraform v5 docs...")
    request = state.get('request', 'Create infrastructure')
    follow_up = state.get('follow_up_prompt')
    user_content = f"User wants: {request}"
    if follow_up:
        user_content += f"\nAdditional context from triage: {follow_up}"
    doc_snippets = state.get('documentation_snippets')
    if doc_snippets:
        user_content += f"\nDocumentation context:\n{doc_snippets}"
    msg = [
        SystemMessage(content=RESEARCHER_PROMPT),
        HumanMessage(content=user_content)
    ]
    response = await llm.ainvoke(msg)
    return {"syntax_guide": response.content}

async def security_agent(state: AgentState):
    """Async Agent: Fetches security policies."""
    print("   -> 🛡️ [Async] CISO is defining security policies...")
    request = state.get('request', 'Create infrastructure')
    follow_up = state.get('follow_up_prompt')
    user_content = f"User wants: {request}"
    if follow_up:
        user_content += f"\nAdditional context from triage: {follow_up}"
    doc_snippets = state.get('documentation_snippets')
    if doc_snippets:
        user_content += f"\nDocumentation context:\n{doc_snippets}"
    msg = [
        SystemMessage(content=SECURITY_PROMPT),
        HumanMessage(content=user_content)
    ]
    response = await llm.ainvoke(msg)
    return {"security_policy": response.content}

async def intelligence_node(state: AgentState):
    """Fan-Out Node: Runs Research and Security in PARALLEL."""
    print("\n⚡ KICKING OFF PARALLEL AGENTS...")
    
    # This is the magic line: It waits for both to finish, but runs them at the same time
    results = await asyncio.gather(
        research_agent(state),
        security_agent(state)
    )
    
    # Merge results back into state
    return {
        "syntax_guide": results[0]["syntax_guide"],
        "security_policy": results[1]["security_policy"],
        "follow_up_prompt": ""
    }

async def architect_node(state: AgentState):
    """The Synthesizer: Takes inputs and writes code."""
    print("   -> 🏗️ Architect is synthesizing code from Research & Policy...")
    
    # Construct the context for the Architect
    request = state.get('request', 'Create infrastructure as requested')
    syntax_guide = state.get('syntax_guide', 'No syntax guide provided')
    security_policy = state.get('security_policy', 'No security policy provided')
    diagnosis = state.get('diagnosis')
    fix_instructions = state.get('fix_instructions')
    
    user_msg = f"""
    Request: {request}
    
    [RESEARCHER'S SYNTAX GUIDE]
    {syntax_guide}
    
    [CISO'S SECURITY POLICY]
    {security_policy}
    
    Errors to fix (if any): {state.get('error', 'None')}
    """

    if diagnosis:
        user_msg += f"\n\n[TRIAGE SUMMARY]\n{diagnosis}"
    if fix_instructions:
        user_msg += f"\n\n[TRIAGE PLAYBOOK]\n{fix_instructions}"
    
    messages = [SystemMessage(content=ARCHITECT_PROMPT), HumanMessage(content=user_msg)]
    response = await llm.ainvoke(messages)
    
    code = response.content.replace("```hcl", "").replace("```", "").strip()
    return {
        "code": code,
        "retry_count": state.get("retry_count", 0) + 1
    }

def tool_node(state: AgentState):
    """Validator: Runs Terraform & Checkov."""
    retry_count = state.get("retry_count", 0)
    print(f"   -> 🛠️ Validating Code (Attempt {retry_count})...")
    
    code = state.get("code", "")
    if not code:
        return {"error": "No code generated", "status": "failed"}
    
    with open("main.tf", "w") as f:
        f.write(code)

    # 1. Syntax Check
    try:
        subprocess.run(["terraform", "init"], check=True, capture_output=True)
        subprocess.run(["terraform", "validate"], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        return {"error": f"Syntax Error: {e.stderr.decode()}", "status": "failed"}

    # 2. Security Check
    try:
        proc = subprocess.run(["checkov", "-f", "main.tf", "--quiet", "--compact"], capture_output=True, text=True)
        if proc.returncode != 0:
            return {"error": f"Security Violations: {proc.stdout}", "status": "failed"}
    except FileNotFoundError:
        pass # Skip if checkov missing

    return {"error": None, "status": "success"}

def discovery_node(state: AgentState):
    """Retrieves fresh Terraform documentation context."""
    request = state.get("request", "terraform aws resource")
    print("   -> 🌐 Discovery agent is searching Terraform docs...")

    urls: List[str] = []
    snippets: List[str] = []

    query = f"site:registry.terraform.io {request}"

    if DDGS is None:
        print("      ⚠️ Install duckduckgo_search for live discovery (pip install duckduckgo-search)")
    else:  # pragma: no cover (network dependant)
        try:
            with DDGS() as ddgs:
                for result in ddgs.text(query, max_results=8):
                    url = result.get("href")
                    if not url or "registry.terraform.io" not in url:
                        continue
                    if url in urls:
                        continue
                    urls.append(url)
                    try:
                        resp = requests.get(url, timeout=10)
                        if resp.ok:
                            snippet = textwrap.shorten(resp.text, width=2500, placeholder="...")
                            snippets.append(f"URL: {url}\n{snippet}")
                    except requests.RequestException:
                        continue
                    if len(urls) >= 3:
                        break
        except Exception as exc:
            print(f"      ⚠️ Discovery lookup failed: {exc}")

    doc_summary = ""
    if snippets:
        combined = "\n\n".join(snippets)[:3500]
        messages = [
            SystemMessage(content=DISCOVERY_PROMPT),
            HumanMessage(content=combined)
        ]
        try:
            summary = llm.invoke(messages)
            doc_summary = summary.content.strip()
        except Exception as exc:
            print(f"      ⚠️ Discovery summarization failed: {exc}")
            doc_summary = combined
    elif urls:
        doc_summary = "\n".join(urls)

    return {
        "documentation_urls": urls,
        "documentation_snippets": doc_summary,
        "follow_up_prompt": state.get("follow_up_prompt", "")
    }

async def triage_node(state: AgentState):
    """Analyzes failures and decides the next remediation path."""
    error_message = state.get("error", "Unknown failure")
    print("   -> 🚑 Triage is analyzing the failure...")

    # Prep payload for triage agent
    payload = {
        "request": state.get("request", ""),
        "error": error_message,
        "recent_code": state.get("code", "")
    }

    messages = [
        SystemMessage(content=TRIAGE_PROMPT),
        HumanMessage(content=json.dumps(payload))
    ]

    response = await llm.ainvoke(messages)
    raw = response.content.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {
            "summary": raw,
            "fix_instructions": raw,
            "needs_additional_research": False,
            "follow_up_prompt": "",
            "should_abort": False
        }

    needs_research = bool(data.get("needs_additional_research"))
    should_abort = bool(data.get("should_abort"))

    if should_abort:
        next_node = "end"
    elif needs_research:
        next_node = "discovery"
    else:
        next_node = "architect"

    status = "aborted" if next_node == "end" else "retry"

    return {
        "diagnosis": data.get("summary", ""),
        "fix_instructions": data.get("fix_instructions", ""),
        "follow_up_prompt": data.get("follow_up_prompt", ""),
        "next_node": next_node,
        "status": status
    }

# --- 5. LOGIC EDGES ---

def decide_after_tool(state: AgentState):
    if state.get("status") == "success":
        print("   -> ✅ Success! Deployment ready.")
        return "success"

    retry_count = state.get("retry_count", 0)
    if retry_count >= 4:
        print("   -> 💀 Max retries reached.")
        return "end"

    print("   -> ❗ Validation failed. Escalating to triage.")
    return "triage"

def decide_after_triage(state: AgentState):
    next_node = state.get("next_node", "architect")
    if next_node not in {"architect", "intelligence", "end", "discovery"}:
        next_node = "architect"
    if next_node == "discovery":
        print("   -> ♻️ Triage requested broader discovery to gather new docs.")
    elif next_node == "intelligence":
        print("   -> 🔁 Triage wants existing intel agents to re-run with new context.")
    elif next_node == "architect":
        print("   -> 🛠️ Triage returned remediation steps for the architect.")
    else:
        print("   -> 🧯 Triage decided to halt automation.")
    return next_node

# --- 6. BUILD GRAPH ---
workflow = StateGraph(AgentState)

# Add nodes - intelligence runs researcher + security in parallel internally
workflow.add_node("discovery", discovery_node)
workflow.add_node("intelligence", intelligence_node)
workflow.add_node("architect", architect_node)
workflow.add_node("tool", tool_node)
workflow.add_node("triage", triage_node)

# Linear flow with discovery in front (intelligence still parallel inside)
workflow.set_entry_point("discovery")
workflow.add_edge("discovery", "intelligence")
workflow.add_edge("intelligence", "architect")
workflow.add_edge("architect", "tool")
workflow.add_conditional_edges(
    "tool",
    decide_after_tool,
    {
        "success": END,
        "triage": "triage",
        "end": END
    }
)

workflow.add_conditional_edges(
    "triage",
    decide_after_triage,
    {
        "discovery": "discovery",
        "intelligence": "intelligence",
        "architect": "architect",
        "end": END
    }
)

app = workflow.compile()

# --- 7. RUNNER ---
async def main():
    print("🚀 Starting Multi-Agent Async Graph...")
    initial_state = {
        "request": "Create an AWS S3 bucket named 'kanu-async-demo'",
        "messages": [],
        "syntax_guide": "",
        "security_policy": "",
        "code": "",
        "error": "",
        "documentation_urls": [],
        "documentation_snippets": "",
        "retry_count": 0,
        "status": "running"
    }
    
    # We use 'await' here because the graph is now async
    result = await app.ainvoke(initial_state)
    
    print("\nFINAL ARCHITECTURE GENERATED:")
    print("=" * 40)
    print(result["code"])
    print("=" * 40)

if __name__ == "__main__":
    asyncio.run(main())