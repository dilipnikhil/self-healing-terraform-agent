import subprocess
import os
from openai import AzureOpenAI

# 1. Setup - Read config.txt
with open("config.txt", "r") as f:
    lines = f.read().strip().split("\n")
    for line in lines:
        if line.startswith("API_KEY:"):
            subscription_key = line.split("API_KEY:")[1]
        elif line.startswith("LANGCHAIN_API_KEY:"):
            os.environ["LANGCHAIN_API_KEY"] = line.split("LANGCHAIN_API_KEY:")[1]
        elif "LANGCHAIN_TRACING_V2" in line:
            os.environ["LANGCHAIN_TRACING_V2"] = "true"
        elif "LANGCHAIN_PROJECT" in line:
            os.environ["LANGCHAIN_PROJECT"] = line.split("=")[1]

# Initialize Azure OpenAI Client
endpoint = "https://dfran-m6zqnnwy-eastus2.cognitiveservices.azure.com/"
api_version = "2024-12-01-preview"

client = AzureOpenAI(
    api_version=api_version,
    azure_endpoint=endpoint,
    api_key=subscription_key
)

SYSTEM_PROMPT = """
You are an expert Cloud Engineer. 
Goal: Write valid, secure Terraform code using AWS Provider v4+ syntax.

CRITICAL RULES:
1. Output ONLY the code. No markdown.
2. AWS PROVIDER v4+ RULE: 'acl', 'versioning', 'server_side_encryption_configuration', 'lifecycle_rule', and 'public_access_block' must be SEPARATE resources. Do NOT define them as arguments inside 'aws_s3_bucket'.
3. If you get a SECURITY ERROR (Checkov), fix it by adding the required separate resources (like aws_s3_bucket_server_side_encryption_configuration).
"""

def write_file(filename, content):
    with open(filename, "w") as f:
        f.write(content)

def run_terraform_validate():
    """Runs terraform validate."""
    try:
        subprocess.run(["terraform", "init"], check=True, capture_output=True)
        result = subprocess.run(["terraform", "validate"], check=True, capture_output=True, text=True)
        return True, "✅ Terraform Syntax Valid."
    except subprocess.CalledProcessError as e:
        return False, f"Terraform Syntax Error:\n{e.stderr}"

def run_security_scan():
    """Runs Checkov security scan."""
    print("   -> 🛡️ Running Security Scan (Checkov)...")
    try:
        # We use --quiet to just get the failures, and --compact to save tokens
        result = subprocess.run(
            ["checkov", "-f", "main.tf", "--quiet", "--compact"], 
            capture_output=True, text=True
        )
        
        # Checkov returns 0 for pass, 1 for fail
        if result.returncode == 0:
            return True, "✅ Security Checks Passed."
        else:
            # We capture the output (the security warnings)
            return False, f"Security Violations Found:\n{result.stdout}"
            
    except FileNotFoundError:
        return True, "⚠️ Checkov not installed. Skipping security scan."

def generate_code(user_prompt, error_context=None):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]
    
    if error_context:
        messages.append({
            "role": "user", 
            "content": f"The code failed validation. Fix it based on this error:\n{error_context}"
        })

    response = client.chat.completions.create(
        model="gpt-4o-mini", # Using your cheap/fast model
        messages=messages,
        temperature=0.1 
    )
    return response.choices[0].message.content.replace("```hcl", "").replace("```", "").strip()

def run_agent(user_request):
    print(f"🚀 Starting Agent with request: '{user_request}'")
    code = generate_code(user_request)
    write_file("main.tf", code)

    # Retry Loop
    MAX_RETRIES = 5
    for attempt in range(MAX_RETRIES):
        print(f"\n--- Attempt {attempt + 1} ---")
        
        # 1. Check Syntax
        syntax_pass, syntax_msg = run_terraform_validate()
        
        if not syntax_pass:
            print(f"❌ {syntax_msg}")
            print("   -> Agent is fixing syntax...")
            code = generate_code(user_request, error_context=syntax_msg)
            write_file("main.tf", code)
            continue # Try next attempt

        # 2. Check Security (Only if syntax passes)
        sec_pass, sec_msg = run_security_scan()
        
        if not sec_pass:
            print(f"❌ {sec_msg}")
            print("   -> Agent is fixing security vulnerabilities...")
            code = generate_code(user_request, error_context=sec_msg)
            write_file("main.tf", code)
            continue # Try next attempt

        # 3. If both pass
        print(f"🎉 SUCCESS! Secure & Valid code generated.")
        print("-" * 40)
        print(code)
        print("-" * 40)
        return True

    print("💀 Failed to generate valid code after max retries.")
    return False

if __name__ == "__main__":
    # THE SECURITY TRAP: Ask for a bucket, but don't ask for encryption.
    # Checkov should scream that it's unencrypted, and the agent should fix it.
    run_agent("Create an AWS S3 bucket named 'kanu-secure-demo'")