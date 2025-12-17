# Self-healing- AI Agent

An AI agent that writes Terraform code from natural language. It validates syntax, runs security checks, and fixes its own mistakes.

## Demo

![Demo Video]()


https://github.com/user-attachments/assets/203f695f-5943-4b54-b689-9fc165176642


*Watch the multi-agent system generate secure Terraform code with automatic retry and fix logic.*

## Screenshots

<img width="672" height="556" alt="Multi-agent workflow architecture" src="https://github.com/user-attachments/assets/c109272d-ffa5-4e5a-9bb1-5464644f2d13" />

## What it does

You ask for infrastructure in plain English. The system:
1. Searches Terraform docs for syntax
2. Checks security requirements
3. Writes the code
4. Validates with `terraform validate` and Checkov
5. If something breaks, it figures out why and tries again

Built with LangGraph, Azure OpenAI, and a bunch of specialist agents working in parallel.

## Setup

```bash
# Install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# You'll also need Terraform CLI installed
# Checkov is optional but recommended
```

Create `config.txt`:
```
API_KEY:your-azure-openai-key
LANGCHAIN_API_KEY:your-langsmith-key
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=kanu
```

Create `.env`:
```
AZURE_OPENAI_API_KEY=your-azure-openai-key
AZURE_OPENAI_ENDPOINT=https://your-endpoint.cognitiveservices.azure.com/
```

## Running it

**Simple version:**
```bash
python agent.py
```

**With validation loop:**
```bash
python orchestrator.py
```

**Full multi-agent system:**
```bash
python graph_agent.py
```

**See the workflow visually:**
```bash
langgraph dev
# Open http://localhost:2024
```

## How it works

The multi-agent version (`graph_agent.py`) uses separate AI agents:

- **Discovery** - searches Terraform docs
- **Researcher** - figures out the right syntax
- **Security Officer** - enforces encryption, access controls, etc.
- **Architect** - writes the actual code
- **Triage** - when things fail, decides how to fix them

The Researcher and Security Officer run in parallel to save time.

## Examples

Edit the request in `graph_agent.py`:

```python
"request": "Create an AWS S3 bucket named 'my-data'"
"request": "Create a PostgreSQL RDS instance with encryption"
"request": "Set up a VPC with public and private subnets"
```

The generated code goes to `main.tf`.

## Files

- `agent.py` - basic single-agent version
- `orchestrator.py` - adds retry logic and validation
- `graph_agent.py` - full multi-agent system
- `langgraph.json` - config for the graph UI

## Notes

- This creates real infrastructure. Review code before running `terraform apply`.
- Without Checkov installed, security scans are skipped.
- The system retries up to 4 times if validation fails.
- Uses AWS Provider v5 syntax (separate resources for encryption, versioning, etc.)

## License

MIT
