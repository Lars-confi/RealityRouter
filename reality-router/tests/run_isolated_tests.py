#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys

# Ensure this script is run from the project root
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)

# Directory used for sandboxed test isolation (excluded from Git)
SANDBOX_DIR = os.path.join(PROJECT_ROOT, "dist_test")


def setup_sandbox():
    """Create a completely clean local directory for isolated test execution."""
    if os.path.exists(SANDBOX_DIR):
        print(f"🧹 Cleaning up existing sandbox at: {SANDBOX_DIR}")
        shutil.rmtree(SANDBOX_DIR)

    os.makedirs(SANDBOX_DIR, exist_ok=True)
    os.makedirs(os.path.join(SANDBOX_DIR, "config"), exist_ok=True)
    os.makedirs(os.path.join(SANDBOX_DIR, "logs"), exist_ok=True)

    print(f"📦 Scaffolded isolated sandbox at: {SANDBOX_DIR}")

    # Write a mocked isolated .env file to ensure the tests never touch live settings
    env_path = os.path.join(SANDBOX_DIR, ".env")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("# Isolated Test Environment Variables - Excluded from Git\n")
        f.write("DEBUG=True\n")
        f.write("REALITY_CHECK_TOKEN=Bearer test_token_xyz_abc_123_456\n")
        f.write("REALITY_CHECK_PROVIDER=Google\n")
        f.write("DEFAULT_STRATEGY=expected_utility\n")
        f.write("DISABLED_MODELS=gemini-3.1-flash-lite,gemini-3-pro-preview\n")
        f.write("SENTIMENT_MODEL_ID=gemini-2.5-flash\n")
        
        # Safely forward any active API keys from the developer's system environment
        # This allows live tests to run successfully without saving keys in version control
        forwarded_keys = [
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "MISTRAL_API_KEY",
            "DEEPSEEK_API_KEY",
            "CUSTOM_LLM_BASE_URL",
            "CUSTOM_LLM_API_KEY"
        ]
        for key in forwarded_keys:
            val = os.environ.get(key)
            if val:
                f.write(f'{key}="{val}"\n')

    print(f"📝 Created sandboxed config `.env` in: {env_path}")


def run_pytest():
    """Run pytest in a sub-process with custom environment settings."""
    print("\n🚀 Executing isolated test suite via pytest...")

    # Set up isolated env vars
    test_env = os.environ.copy()
    test_env["REALITY_ROUTER_HOME"] = SANDBOX_DIR
    test_env["PYTHONPATH"] = f"{PROJECT_ROOT}:{test_env.get('PYTHONPATH', '')}"

    # Determine correct python/pytest executable (use venv if available)
    venv_pytest = os.path.join(PROJECT_ROOT, "venv", "bin", "pytest")
    if not os.path.exists(venv_pytest):
        venv_pytest = os.path.join(PROJECT_ROOT, ".venv", "bin", "pytest")
    if not os.path.exists(venv_pytest):
        # Fallback to system pytest
        venv_pytest = "pytest"

    cmd = [
        venv_pytest,
        SCRIPT_DIR,
        "-v",
        "-s",
    ]

    try:
        result = subprocess.run(cmd, env=test_env, check=False)
        print(f"\n✨ Isolated Pytest process finished with status code: {result.returncode}")
        return result.returncode
    except Exception as e:
        print(f"❌ Failed to run pytest: {e}")
        return 1


def main():
    print("=====================================================")
    print("      Reality Router Isolated Integration Harness     ")
    print("=====================================================")
    
    setup_sandbox()
    status = run_pytest()
    
    # We keep the sandbox directory intact so developers can inspect the DB and logs after a run.
    print(f"\n📂 Sandbox workspace left intact at: {SANDBOX_DIR}")
    print("=====================================================")
    sys.exit(status)


if __name__ == "__main__":
    main()
