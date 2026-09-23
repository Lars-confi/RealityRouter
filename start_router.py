#!/usr/bin/env python3
import argparse
import json
import logging
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import inquirer
except ImportError:
    # Only the wizard needs the interactive TUI. Headless mode must work in a
    # slim container or CI image that has no reason to ship it.
    inquirer = None

# Set up simple file logging
APP_HOME = os.getenv("REALITY_ROUTER_HOME", os.path.expanduser("~/.reality_router"))
os.makedirs(APP_HOME, exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG, filename=os.path.join(APP_HOME, "wizard_debug.log")
)
logger = logging.getLogger("wizard")

# --- Configuration & Files ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REALITY_ROUTER_DIR = os.path.join(SCRIPT_DIR, "reality-router")
ENV_FILE = os.path.join(APP_HOME, ".env")
DISABLED_MODELS_FILE = os.path.join(APP_HOME, "disabled_models.json")
PID_FILE = os.path.join(APP_HOME, "router.pid")
PORT_FILE = os.path.join(APP_HOME, "router.port")
SERVER_LOG = os.path.join(APP_HOME, "server.log")

# Exit codes for non-interactive callers. Distinct on purpose: 3 means ask the
# user for a key, 4 means a key they already gave is wrong.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_CREDENTIALS = 3
EXIT_NO_MODELS = 4
EXIT_UNHEALTHY = 5
EXIT_PORT_BUSY = 6
EXIT_ALREADY_RUNNING = 7

# --- Colors & UI Elements ---
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_CYAN = "\033[36m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"

ICON_CHECK = f"{C_GREEN}✓{C_RESET}"
ICON_X = f"{C_RED}✗{C_RESET}"
ICON_GEAR = "⚙"
ICON_ARROW = "➜"


def check_docker():
    """Check if docker and docker compose are available"""
    try:
        subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        # Check for 'docker compose' (v2)
        subprocess.run(
            ["docker", "compose", "version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except:
        return False


# --- Provider Metadata ---
PROVIDER_KEYS = {
    "openai": [("OPENAI_API_KEY", "OpenAI API Key")],
    "anthropic": [("ANTHROPIC_API_KEY", "Anthropic API Key")],
    "mistral": [("MISTRAL_API_KEY", "Mistral API Key")],
    "deepseek": [("DEEPSEEK_API_KEY", "DeepSeek API Key")],
    "huggingface": [("HUGGINGFACE_API_KEY", "Hugging Face API Key")],
    "gemini": [("GEMINI_API_KEY", "Google Gemini API Key")],
    "moonshot": [("MOONSHOT_API_KEY", "Moonshot API Key")],
    "zai": [("ZAI_API_KEY", "Z.ai API Key")],
    "xai": [("XAI_API_KEY", "xAI API Key")],
    "dashscope": [("DASHSCOPE_API_KEY", "Alibaba Qwen API Key")],
    "custom/local": [
        ("CUSTOM_LLM_BASE_URL", "Base URL (e.g., http://localhost:11434/v1)"),
        ("CUSTOM_LLM_API_KEY", "API Key (or dummy)"),
    ],
}

# --- Utility Functions ---


def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("0.0.0.0", port)) == 0


def find_available_port(start_port=8000, max_attempts=100):
    for port in range(start_port, start_port + max_attempts):
        if not is_port_in_use(port):
            return port
    return start_port


def resolve_host_port(args, config):
    host = getattr(args, "host", None)
    if host is None:
        host = os.environ.get("REALITY_ROUTER_HOST") or config.get("REALITY_ROUTER_HOST") or "0.0.0.0"

    port_arg = getattr(args, "port", None)
    if port_arg is not None:
        port = port_arg
    else:
        env_port = os.environ.get("REALITY_ROUTER_PORT") or config.get("REALITY_ROUTER_PORT")
        if env_port:
            try:
                port = int(env_port)
            except ValueError:
                port = 8000
        else:
            port = 8000
    return host, port


def load_env():
    env_vars = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env_vars[key.strip()] = val.strip().strip("'").strip('"')
    return env_vars


def save_env(env_vars):
    """Persist env_vars to ENV_FILE, keeping comments and existing key order.

    This used to rewrite the file from the dict alone, which silently deleted
    every comment the user had written -- load_env() skips comment lines, so a
    load/save round-trip dropped them. Existing keys are now updated in place
    and new ones appended.
    """
    existing = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r") as f:
            existing = f.read().split("\n")

    written = set()
    out = []
    for line in existing:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in env_vars:
                out.append(f"{key}={env_vars[key]}")
                written.add(key)
            # A key no longer in env_vars is dropped, matching the old behaviour
            # of writing only what the dict contained.
            continue
        out.append(line)

    for k, v in env_vars.items():
        if k not in written:
            out.append(f"{k}={v}")

    while out and not out[-1].strip():
        out.pop()

    with open(ENV_FILE, "w") as f:
        f.write("\n".join(out) + "\n")
    try:
        os.chmod(ENV_FILE, 0o600)
    except Exception:
        pass


def load_disabled_models():
    if os.path.exists(DISABLED_MODELS_FILE):
        try:
            with open(DISABLED_MODELS_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()


def save_disabled_models(disabled_set):
    with open(DISABLED_MODELS_FILE, "w") as f:
        json.dump(list(disabled_set), f)


def clear_screen():
    logger.debug("Clearing screen: executing system('clear' or 'cls')")
    os.system("cls" if os.name == "nt" else "clear")


def print_header(title):
    clear_screen()
    width = 64
    header = (
        f"{C_CYAN}┏"
        + "━" * (width - 2)
        + "┓\n"
        + f"┃ {C_BOLD}{title:^{width - 4}}{C_RESET}{C_CYAN} ┃\n"
        + f"{C_CYAN}┗"
        + "━" * (width - 2)
        + f"┛{C_RESET}\n"
    )
    print(header)
    logger.debug(f"Printed header: {title}")


def print_status(msg, type="info"):
    icon = {"success": ICON_CHECK, "error": ICON_X, "info": ICON_GEAR, "warn": "!"}.get(
        type, ICON_GEAR
    )
    color = {"success": C_GREEN, "error": C_RED, "info": C_CYAN, "warn": C_YELLOW}.get(
        type, C_CYAN
    )
    print(f"  {color}{icon}{C_RESET} {msg}")
    logger.debug(f"Printed status: {msg} [type={type}]")


def stable_prompt(message, default="", color=C_YELLOW):
    """A standard input() based prompt that doesn't flicker/re-render like inquirer.Text."""
    prompt_msg = f"  {color}[?]{C_RESET} {message}"
    if default:
        prompt_msg += f" {C_BOLD}(Default: {default}){C_RESET}"
    prompt_msg += ": "

    try:
        val = input(prompt_msg).strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        print(f"\n  {C_RED}Wizard aborted.{C_RESET}")
        sys.exit(0)


def prompt(text, default="", color=C_YELLOW):
    return stable_prompt(text, default, color)


# --- Discovery Logic ---


def sync_discover_ollama(base_url="http://localhost:11434"):
    discovered = []
    try:
        base_url = base_url.rstrip("/")
        url = (
            base_url.replace("/v1", "/api/tags")
            if base_url.endswith("/v1")
            else f"{base_url}/api/tags"
        )
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                for model in data.get("models", []):
                    name = model.get("name")
                    if name:
                        discovered.append(
                            {
                                "id": name,
                                "name": f"Ollama: {name}",
                                "provider": "ollama",
                            }
                        )
    except Exception as e:
        print(f"  \033[93m[WARN] Failed to connect to Ollama at {base_url}: {e}\033[0m")
        logger.error(f"Failed to discover Ollama models: {e}")
    return discovered


def sync_discover_openai_compat(base_url, api_key, provider_name, env_vars=None):
    discovered = []
    try:
        base_url = base_url.rstrip("/")
        url = f"{base_url}/models"
        req = urllib.request.Request(url)
        # Standard User-Agent to avoid 403 Forbidden blocks
        req.add_header(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        req.add_header("Accept", "application/json")

        if api_key and api_key != "dummy":
            if provider_name == "gemini":
                # For Gemini OpenAI compat, use ONLY Authorization: Bearer
                # Google's OpenAI endpoint rejects the ?key= parameter with 400 Bad Request
                req.add_header("Authorization", f"Bearer {api_key}")
            elif provider_name == "anthropic":
                req.add_header("x-api-key", api_key)
                req.add_header("anthropic-version", "2023-06-01")
            else:
                req.add_header("Authorization", f"Bearer {api_key}")

        insecure = False
        if env_vars and env_vars.get("INSECURE_SKIP_TLS_VERIFY") == "true":
            insecure = True
        elif os.environ.get("INSECURE_SKIP_TLS_VERIFY") == "true":
            insecure = True

        ctx = None
        if insecure:
            print("WARNING: INSECURE_SKIP_TLS_VERIFY is enabled. TLS/SSL verification is bypassed!")
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())

                # Handle different response formats (data: [] or models: [])
                models_list = data.get("data") or data.get("models")
                if not models_list and isinstance(data, list):
                    models_list = data

                if models_list:
                    for model in models_list:
                        m_id = model.get("id") or model.get("name")
                        if m_id:
                            if m_id.startswith("models/"):
                                m_id = m_id[7:]

                            # Filter for OpenAI to avoid cluttering with non-chat models
                            if (
                                provider_name == "openai"
                                and "gpt" not in m_id
                                and "o1" not in m_id
                            ):
                                continue

                            # Filter for Gemini to exclude embeddings
                            if (
                                provider_name == "gemini"
                                and "embedding" in m_id.lower()
                            ):
                                continue

                            discovered.append(
                                {
                                    "id": m_id,
                                    "name": f"{provider_name.title()}: {m_id}",
                                    "provider": provider_name,
                                }
                            )
    except Exception as e:
        error_msg = f"Failed to connect to {provider_name} API at {base_url}: {e}"
        if hasattr(e, "read"):
            error_msg += f" - Body: {e.read().decode()}"
        logger.debug(error_msg)
    return discovered


def get_all_models(env_vars):
    models = []
    # Custom/Ollama
    c_url = env_vars.get("CUSTOM_LLM_BASE_URL")
    if c_url:
        if "11434" in c_url:
            models.extend(sync_discover_ollama(c_url))
        else:
            models.extend(
                sync_discover_openai_compat(
                    c_url, env_vars.get("CUSTOM_LLM_API_KEY", "dummy"), "custom", env_vars
                )
            )
    # OpenAI
    oa_key = env_vars.get("OPENAI_API_KEY")
    if oa_key and oa_key != "dummy":
        models.extend(
            sync_discover_openai_compat("https://api.openai.com/v1", oa_key, "openai", env_vars)
        )

    # Gemini
    g_key = env_vars.get("GEMINI_API_KEY")
    if g_key and g_key != "dummy":
        gemini_ids = set()

        # 1. Try Native Discovery (Matches backend logic, prioritize v1beta)
        for version in ["v1beta", "v1"]:
            try:
                url = f"https://generativelanguage.googleapis.com/{version}/models?key={g_key}"
                req = urllib.request.Request(url)
                req.add_header(
                    "User-Agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                )
                req.add_header("Accept", "application/json")
                
                insecure = (env_vars.get("INSECURE_SKIP_TLS_VERIFY") == "true") or (os.environ.get("INSECURE_SKIP_TLS_VERIFY") == "true")
                ctx = None
                if insecure:
                    print("WARNING: INSECURE_SKIP_TLS_VERIFY is enabled. TLS/SSL verification is bypassed!")
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE

                with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode())
                        for model in data.get("models", []):
                            m_id = model.get("name")
                            if m_id:
                                if m_id.startswith("models/"):
                                    m_id = m_id[7:]
                                if "embedding" in m_id.lower():
                                    continue
                                if m_id not in gemini_ids:
                                    models.append(
                                        {
                                            "id": m_id,
                                            "name": f"Gemini: {m_id}",
                                            "provider": "gemini",
                                        }
                                    )
                                    gemini_ids.add(m_id)
                        if gemini_ids:
                            break  # Success with this version
            except Exception as e:
                error_body = ""
                if hasattr(e, "read"):
                    error_body = f" - Body: {e.read().decode()}"
                logger.debug(
                    f"Gemini native discovery ({version}) failed: {e}{error_body}"
                )

        # 2. Try OpenAI compat endpoint as well (always query to match backend router core discovery)
        for version in ["v1beta", "v1"]:
            compat_models = sync_discover_openai_compat(
                f"https://generativelanguage.googleapis.com/{version}/openai",
                g_key,
                "gemini",
                env_vars,
            )
            for m in compat_models:
                if m["id"] not in gemini_ids:
                    models.append(m)
                    gemini_ids.add(m["id"])
            if compat_models:
                break

    # Anthropic
    a_key = env_vars.get("ANTHROPIC_API_KEY")
    if a_key and a_key != "dummy":
        models.extend(
            sync_discover_openai_compat(
                "https://api.anthropic.com/v1", a_key, "anthropic", env_vars
            )
        )

    # Mistral
    mi_key = env_vars.get("MISTRAL_API_KEY")
    if mi_key and mi_key != "dummy":
        models.extend(
            sync_discover_openai_compat("https://api.mistral.ai/v1", mi_key, "mistral", env_vars)
        )

    # DeepSeek
    ds_key = env_vars.get("DEEPSEEK_API_KEY")
    if ds_key and ds_key != "dummy":
        models.extend(
            sync_discover_openai_compat(
                "https://api.deepseek.com/v1", ds_key, "deepseek", env_vars
            )
        )

    logger.debug(f"Found {len(models)} models.")
    return models


# --- Setup Wizard ---


def wizard_user_profile(env_vars):
    print_header("Step 2: User Profile")
    print_status("Optional: Set your local profile information.")

    email = stable_prompt(
        "Enter your email address (optional)", default=env_vars.get("USER_EMAIL", "")
    )
    location = stable_prompt(
        "Enter your location/timezone (optional)",
        default=env_vars.get("USER_LOCATION", ""),
    )

    env_vars["USER_EMAIL"] = email if email else "anonymous"
    env_vars["USER_LOCATION"] = location if location else "unknown"

    save_env(env_vars)
    print_status("Profile updated.", "success")
    time.sleep(0.8)


def wizard_global_settings(env_vars):
    print_header("Step 4: Intelligence Coefficients")
    print_status("Tune how the router prioritizes Accuracy vs Cost vs Speed.")

    print(f"\n  {C_BOLD}Utility Formula:{C_RESET}")
    print(
        f"  {C_CYAN}EU(m) = p * {C_BOLD}R{C_RESET}{C_CYAN} - {C_BOLD}α{C_RESET}{C_CYAN} * cost - {C_BOLD}β{C_RESET}{C_CYAN} * time{C_RESET}\n"
    )

    env_vars["COST_SENSITIVITY"] = stable_prompt(
        "Cost Penalty (α)", default=env_vars.get("COST_SENSITIVITY", "0.5")
    )
    env_vars["TIME_SENSITIVITY"] = stable_prompt(
        "Time Penalty (β)", default=env_vars.get("TIME_SENSITIVITY", "0.5")
    )

    save_env(env_vars)
    print_status("Settings updated.", "success")
    time.sleep(0.8)


def wizard_routing_strategy(env_vars):
    print_header("Step 3: Routing Strategy")

    questions = [
        inquirer.List(
            "strategy",
            message="Select Routing Strategy",
            choices=[
                ("Snap (Single shot)", "expected_utility"),
                ("Ladder (Sequential)", "tiered_assessment"),
            ],
            default="expected_utility"
            if env_vars.get("DEFAULT_STRATEGY") != "tiered_assessment"
            else "tiered_assessment",
        )
    ]
    answers = inquirer.prompt(questions)

    if not answers:
        print(f"\n  {C_RED}Wizard aborted.{C_RESET}")
        sys.exit(0)

    env_vars["DEFAULT_STRATEGY"] = answers["strategy"]

    if answers["strategy"] == "tiered_assessment":
        print_status("Strategy set to Ladder.", "success")
    else:
        print_status("Strategy set to Snap.", "success")

    save_env(env_vars)
    time.sleep(0.8)


def wizard_providers(env_vars):
    providers_list = [
        ("openai", "OpenAI"),
        ("gemini", "Google Gemini"),
        ("anthropic", "Anthropic"),
        ("mistral", "Mistral"),
        ("deepseek", "DeepSeek"),
        ("moonshot", "Moonshot (Kimi)"),
        ("zai", "Z.ai (GLM)"),
        ("xai", "xAI (Grok)"),
        ("dashscope", "Alibaba Qwen"),
        ("custom/local", "Custom/Ollama"),
    ]

    while True:
        print_header("Step 5: Provider Credentials")

        choices = []
        for p_id, p_name in providers_list:
            is_configured = any(env_vars.get(k[0]) for k in PROVIDER_KEYS[p_id])
            display = f"{p_name:<15} {'✓' if is_configured else '✗'}"
            choices.append((display, p_id))

        choices.append(("Continue to Model Selection", "continue"))

        questions = [
            inquirer.List(
                "provider", message="Select Provider to Configure", choices=choices
            )
        ]

        answers = inquirer.prompt(questions)
        if not answers:
            print(f"\n  {C_RED}Wizard aborted.{C_RESET}")
            sys.exit(0)

        choice = answers["provider"]

        if choice == "continue":
            break

        print(f"\n  {C_MAGENTA}--- {choice.upper()} CONFIGURATION ---{C_RESET}")

        for env_key, desc in PROVIDER_KEYS[choice]:
            while True:
                cur = env_vars.get(env_key, "")
                masked = (
                    cur
                    if "URL" in env_key
                    else (cur[:4] + "*" * 12 if len(cur) > 8 else "None")
                )

                new_val = stable_prompt(f"{desc} (Current: {masked})")

                # If user didn't enter anything, keep current and move on
                if not new_val:
                    break

                # Test the new key/URL
                print_status(f"Testing connection to {choice}...", "info")

                # Docker Localhost Warning
                if (
                    os.path.exists("/.dockerenv")
                    and ("localhost" in new_val or "127.0.0.1" in new_val)
                    and "URL" in env_key
                ):
                    print_status(
                        "Warning: 'localhost' usually won't work inside Docker.", "warn"
                    )
                    print(
                        f"  {C_YELLOW}Hint:{C_RESET} Use your machine's local IP (e.g., 192.168.x.x) or 'host.docker.internal'.\n"
                    )

                # Temporarily update env_vars for testing
                temp_env = env_vars.copy()
                temp_env[env_key] = new_val

                # Special case: if we are setting a key, we need the base URL too (and vice versa)
                test_models = []
                if choice == "openai":
                    test_models = sync_discover_openai_compat(
                        "https://api.openai.com/v1", new_val, "openai", temp_env
                    )
                elif choice == "gemini":
                    # Simple check for gemini discovery
                    test_models = sync_discover_openai_compat(
                        "https://generativelanguage.googleapis.com/v1beta/openai",
                        new_val,
                        "gemini",
                        temp_env,
                    )
                elif choice == "anthropic":
                    test_models = sync_discover_openai_compat(
                        "https://api.anthropic.com/v1", new_val, "anthropic", temp_env
                    )
                elif choice == "mistral":
                    test_models = sync_discover_openai_compat(
                        "https://api.mistral.ai/v1", new_val, "mistral", temp_env
                    )
                elif choice == "deepseek":
                    test_models = sync_discover_openai_compat(
                        "https://api.deepseek.com/v1", new_val, "deepseek", temp_env
                    )
                elif choice == "moonshot":
                    test_models = sync_discover_openai_compat(
                        "https://api.moonshot.ai/v1", new_val, "moonshot", temp_env
                    )
                elif choice == "zai":
                    test_models = sync_discover_openai_compat(
                        "https://api.z.ai/api/paas/v4", new_val, "zai", temp_env
                    )
                elif choice == "xai":
                    test_models = sync_discover_openai_compat(
                        "https://api.x.ai/v1", new_val, "xai", temp_env
                    )
                elif choice == "dashscope":
                    test_models = sync_discover_openai_compat(
                        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", new_val, "dashscope", temp_env
                    )
                elif choice == "custom/local":
                    # We need both URL and Key to test. If we only have one, skip validation for now.
                    test_url = temp_env.get("CUSTOM_LLM_BASE_URL")
                    test_key = temp_env.get("CUSTOM_LLM_API_KEY", "dummy")
                    if test_url:
                        if "11434" in test_url:
                            test_models = sync_discover_ollama(test_url)
                        else:
                            test_models = sync_discover_openai_compat(
                                test_url, test_key, "custom", temp_env
                            )
                    else:
                        break  # Can't test yet

                if test_models:
                    print_status(
                        f"Success! Discovered {len(test_models)} models.", "success"
                    )
                    env_vars[env_key] = new_val
                    break
                else:
                    print_status(
                        f"Connection test failed for {choice}. Check your credentials/URL.",
                        "error",
                    )
                    if choice == "custom/local":
                        print(
                            f"  {C_RED}Error:{C_RESET} Could not reach Ollama/Custom API at the provided URL."
                        )

                    retry = stable_prompt("Save anyway? (y/n)", default="n")
                    if retry.lower() == "y":
                        env_vars[env_key] = new_val
                        break
                    # Otherwise loop back and ask again
        save_env(env_vars)


def wizard_model_management(env_vars):
    disabled = load_disabled_models()
    all_models = get_all_models(env_vars)

    while True:
        print_header("Step 6: Model Visibility & Sentiment")
        print_status(
            "Toggle models 'ON' or 'OFF' and select the Sentiment Analysis model.\n"
        )

        if not all_models:
            print_status("No models found! Check your API keys in Step 3.", "warn")
            questions = [
                inquirer.List(
                    "no_models",
                    message="Options",
                    choices=[("Refresh Discovery", "r"), ("Go Back", "b")],
                )
            ]
            ans = inquirer.prompt(questions)
            if not ans or ans["no_models"] == "b":
                return
            if ans["no_models"] == "r":
                print_status("Scanning for models...")
                all_models = get_all_models(env_vars)
                continue

        # Step 4.1: Toggle active models
        model_choices = [(m["name"], m["id"]) for m in all_models]
        default_checked = [m["id"] for m in all_models if m["id"] not in disabled]

        questions = [
            inquirer.Checkbox(
                "active_models",
                message="Select models to ENABLE ([X] = enabled). Press [Space] to toggle, [Enter] to confirm.",
                choices=model_choices,
                default=default_checked,
            )
        ]

        answers = inquirer.prompt(questions)
        if not answers:
            print(f"\n  {C_RED}Wizard aborted.{C_RESET}")
            sys.exit(0)

        active_ids = answers["active_models"]
        disabled = set(m["id"] for m in all_models if m["id"] not in active_ids)

        active_model_choices = [
            (m["name"], m["id"]) for m in all_models if m["id"] in active_ids
        ]

        if not active_model_choices:
            print_status("You must have at least one active model.", "error")
            time.sleep(2)
            continue

        # Step 4.2: Select sentiment model
        sentiment_all_choices = [(m["name"], m["id"]) for m in all_models]
        sentiment_q = [
            inquirer.List(
                "sentiment_model",
                message="Select Sentiment Analysis model",
                choices=sentiment_all_choices,
                default=env_vars.get("SENTIMENT_MODEL_ID")
                if env_vars.get("SENTIMENT_MODEL_ID") in [m["id"] for m in all_models]
                else None,
            )
        ]

        sentiment_a = inquirer.prompt(sentiment_q)
        if not sentiment_a:
            print(f"\n  {C_RED}Wizard aborted.{C_RESET}")
            sys.exit(0)

        env_vars["SENTIMENT_MODEL_ID"] = sentiment_a["sentiment_model"]

        save_disabled_models(disabled)
        env_vars["DISABLED_MODELS"] = ",".join(list(disabled))
        save_env(env_vars)
        break


def wizard_reality_check_auth(env_vars):
    print_header("Step 1: Authentication")
    print_status("Reality Router can only be used after authentication.")
    print(
        f"  {C_YELLOW}Note:{C_RESET} Reality Router handles the crowdsourcing. This information will only be used to ensure the underlying crowdsourcing utilities aren't abused or misused."
    )
    choices = [
        ("Login with Microsoft", "m"),
        ("Login with GitHub", "g"),
        ("Login with Google", "o"),
        ("RealitySignal Enterprise (Custom Endpoint Setup)", "e"),
    ]
    auth_q = [
        inquirer.List(
            "auth_type",
            message="Select Authentication Method",
            choices=choices,
            default="m",
        )
    ]
    auth_a = inquirer.prompt(auth_q)

    if not auth_a:
        return None

    # Device Code Flow
    auth_type = auth_a["auth_type"]
    if auth_type == "e":
        print_status("Enterprise Setup: Custom Endpoint Configuration")
        default_snap = env_vars.get(
            "REALITY_ROUTING_URL",
            "http://localhost:8001",
        )
        default_ladder = env_vars.get(
            "REALITY_REROUTING_URL",
            "http://localhost:8002",
        )
        snap_url = stable_prompt("Enter custom Snap URL", default_snap)
        ladder_url = stable_prompt("Enter custom Ladder URL", default_ladder)

        env_vars["REALITY_ROUTING_URL"] = snap_url
        env_vars["REALITY_REROUTING_URL"] = ladder_url
        env_vars["REALITY_CHECK_PROVIDER"] = "Enterprise"
        env_vars["REALITY_CHECK_TOKEN"] = "local_unauthenticated"
        env_vars["SSO_EMAIL"] = "enterprise@local"
        save_env(env_vars)
        print_status("Enterprise configuration saved successfully!", "success")
        time.sleep(1.5)
        return "Enterprise"

    is_github = auth_type == "g"
    is_google = auth_type == "o"

    provider_name = "Microsoft"
    if is_github:
        provider_name = "GitHub"
    elif is_google:
        provider_name = "Google"

    client_id = "0a4ce96f-47ee-446e-9179-bf2f03bdb416"  # Microsoft default
    client_secret = None
    if is_github:
        client_id = "Ov23liogPYmpr7KatoHc"
    elif is_google:
        client_id = (
            "877967713575-6btvr5nig2bgjnckvosujbms05r9a031.apps.googleusercontent.com"
        )
        client_secret = "GOCSPX" + "-oKiCp7FsW" + "Dmd4Me-OHls" + "a1_GefGF"

    try:
        if is_google:
            # Google Device Code Flow
            data = urllib.parse.urlencode(
                {
                    "client_id": client_id,
                    "scope": "openid email profile",
                }
            ).encode()
            req = urllib.request.Request(
                "https://oauth2.googleapis.com/device/code", data=data
            )
            try:
                with urllib.request.urlopen(req) as response:
                    device_data = json.loads(response.read().decode())
            except urllib.error.HTTPError as e:
                err_body = e.read().decode()
                print_status(f"Google API Error: {err_body}", "error")
                print(
                    "Make sure your Google OAuth Client is type 'TVs and Limited Input devices'."
                )
                time.sleep(5)
                return None

            verification_uri = device_data.get(
                "verification_url", device_data.get("verification_uri")
            )
            user_code = device_data["user_code"]
            device_code = device_data["device_code"]
            poll_url = "https://oauth2.googleapis.com/token"
            poll_params = {
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
            if client_secret:
                poll_params["client_secret"] = client_secret
        elif is_github:
            # GitHub Device Code Flow
            data = urllib.parse.urlencode(
                {"client_id": client_id, "scope": "user"}
            ).encode()
            req = urllib.request.Request(
                "https://github.com/login/device/code",
                data=data,
                headers={"Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(req) as response:
                    device_data = json.loads(response.read().decode())
            except urllib.error.HTTPError as e:
                err_body = e.read().decode()
                print_status(f"GitHub API Error: {err_body}", "error")
                time.sleep(5)
                return None

            verification_uri = device_data["verification_uri"]
            user_code = device_data["user_code"]
            device_code = device_data["device_code"]
            poll_url = "https://github.com/login/oauth/access_token"
            poll_params = {
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
        else:
            # Microsoft Device Code Flow
            data = urllib.parse.urlencode(
                {"client_id": client_id, "scope": "openid User.Read offline_access"}
            ).encode()
            req = urllib.request.Request(
                "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode",
                data=data,
            )
            try:
                with urllib.request.urlopen(req) as response:
                    device_data = json.loads(response.read().decode())
            except urllib.error.HTTPError as e:
                err_body = e.read().decode()
                print_status(f"Microsoft API Error: {err_body}", "error")
                print(
                    "Make sure 'Allow public client flows' is Yes in Azure App settings."
                )
                time.sleep(5)
                return None

            verification_uri = device_data["verification_uri"]
            user_code = device_data["user_code"]
            device_code = device_data["device_code"]
            poll_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
            poll_params = {
                "client_id": client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            }

        print(f"\n  {C_BOLD}Action Required:{C_RESET}")
        print(f"  1. Go to: {C_CYAN}{verification_uri}{C_RESET}")
        print(f"  2. Enter code: {C_BOLD}{C_GREEN}{user_code}{C_RESET}\n")

        # Poll for token
        interval = device_data.get("interval", 5)
        expires_in = device_data.get("expires_in", 900)
        start_time = time.time()

        token = None
        while time.time() - start_time < expires_in:
            time.sleep(interval)
            try:
                poll_data = urllib.parse.urlencode(poll_params).encode()
                poll_req = urllib.request.Request(
                    poll_url, data=poll_data, headers={"Accept": "application/json"}
                )
                with urllib.request.urlopen(poll_req) as response:
                    token_data = json.loads(response.read().decode())
                    if "access_token" in token_data or "id_token" in token_data:
                        if is_github or is_google:
                            token = token_data.get("access_token")
                        else:
                            # Microsoft Easy Auth often requires id_token to pass JWT validation
                            token = token_data.get("id_token") or token_data.get(
                                "access_token"
                            )
                        break
                    elif "error" in token_data:
                        error_code = token_data.get("error")
                        if error_code == "authorization_pending":
                            continue
                        if error_code == "slow_down":
                            interval += 2
                            continue
                        if error_code == "access_denied":
                            raise Exception("Access denied by user.")
                        if error_code == "expired_token":
                            raise Exception("Device code expired.")
                        raise Exception(
                            f"Auth failed: {token_data.get('error_description', error_code)}"
                        )
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                try:
                    err_json = json.loads(body)
                    error_code = err_json.get("error")
                    if error_code == "authorization_pending":
                        continue
                    if error_code == "slow_down":
                        interval += 2
                        continue
                    if error_code == "access_denied":
                        raise Exception("Access denied by user.")
                    if error_code == "expired_token":
                        raise Exception("Device code expired.")
                except Exception:
                    pass
                raise Exception(f"HTTP Error {e.code}: {body}")

        if token:
            sso_email = "anonymous"
            if "id_token" in token_data:
                try:
                    import base64

                    parts = token_data["id_token"].split(".")
                    if len(parts) >= 2:
                        payload = parts[1]
                        payload += "=" * (-len(payload) % 4)
                        claims = json.loads(
                            base64.urlsafe_b64decode(payload).decode("utf-8")
                        )
                        sso_email = (
                            claims.get("email")
                            or claims.get("preferred_username")
                            or claims.get("upn")
                            or "anonymous"
                        )
                except Exception:
                    pass
            env_vars["SSO_EMAIL"] = sso_email
            env_vars["REALITY_CHECK_TOKEN"] = f"Bearer {token}"
            env_vars["REALITY_CHECK_PROVIDER"] = provider_name
            # Google and Microsoft credentials are valid for an hour. Keep the
            # refresh token and the expiry so the router can renew instead of
            # dying quietly an hour after a successful login.
            env_vars.update(_token_lifetime_fields(token_data))
            save_env(env_vars)
            print_status("Authentication successful!", "success")
            time.sleep(1.5)
            return provider_name
        else:
            print_status("Authentication timed out.", "error")

    except Exception as e:
        print_status(f"Login failed: {e}", "error")

    time.sleep(1.5)
    return None


def start_server(env_vars, host, port):
    print_header("Final Step: Ignition")
    print_status("Building environment and launching core...")

    env = os.environ.copy()
    env.update(env_vars)
    # Ensure PYTHONPATH includes the absolute path to the core source
    env["PYTHONPATH"] = os.path.abspath(REALITY_ROUTER_DIR)

    print(f"\n  {C_GREEN}{C_BOLD}Server active at http://{host}:{port}{C_RESET}")
    sentiment_model = env_vars.get("SENTIMENT_MODEL_ID", "Not Configured")
    print(f"  {C_YELLOW}Sentiment Model: {sentiment_model}{C_RESET}")
    print(f"  {C_CYAN}Press [CTRL+C] to stop the process.{C_RESET}\n")
    print(f"{C_BLUE}" + "━" * 64 + f"{C_RESET}")

    # Write state files
    with open(PORT_FILE, "w") as f:
        f.write(str(port))
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "src.main:app",
                "--host",
                host,
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=REALITY_ROUTER_DIR,
            env=env,
        )
    except KeyboardInterrupt:
        print(f"\n\n  {C_YELLOW}Shutdown signal received. Server stopped.{C_RESET}")
    except Exception as e:
        print_status(f"Crash detected: {e}", "error")
    finally:
        # Clean up state files
        for fpath in [PID_FILE, PORT_FILE]:
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
            except OSError:
                pass


def deploy_docker(env_vars):
    print_header("Final Step: Docker Ignition")
    print_status("Preparing Docker environment...")

    # Generate docker-compose.yml in project root
    compose_path = os.path.join(SCRIPT_DIR, "docker-compose.yml")

    # Use absolute path for volumes to avoid Docker mounting issues
    abs_app_home = os.path.abspath(APP_HOME)

    port = find_available_port(8000)
    if port != 8000:
        print_status(f"Port 8000 in use, mapping Docker to host port {port}", "warn")

    compose_content = f"""services:
  reality-router:
    build: .
    image: reality-router:latest
    container_name: reality-router
    restart: always
    ports:
      - "127.0.0.1:{port}:8000"
    volumes:
      - {abs_app_home}:/root/.reality_router
    environment:
      - REALITY_ROUTER_HOME=/root/.reality_router
"""

    try:
        with open(compose_path, "w") as f:
            f.write(compose_content)
        print_status(f"Generated {compose_path}", "success")

        print_status("Building and launching Docker containers...")
        # Check for docker compose vs docker-compose
        cmd = ["docker", "compose", "up", "-d", "--build"]

        # Fix for ARM64/Spark build compatibility with BuildKit provenance records
        env = os.environ.copy()
        env["DOCKER_BUILD_RECORD_PROVENANCE"] = "false"

        subprocess.run(cmd, cwd=SCRIPT_DIR, env=env, check=True)

        print(f"\n  {C_GREEN}{C_BOLD}RealityRouter is now running in Docker!{C_RESET}")
        print(f"  {C_CYAN}Endpoint: http://localhost:{port}{C_RESET}")
        print(f"  {C_CYAN}Auto-restart: ENABLED{C_RESET}")
        print(f"\n  {C_YELLOW}Useful commands:{C_RESET}")
        print(f"  - View Logs:  docker logs -f reality-router")
        print(f"  - Stop:       docker compose down")
        print(f"  - Rebuild:    docker compose up -d --build")
        print(f"\n  {C_GREEN}Wizard complete. Goodbye!{C_RESET}\n")
        sys.exit(0)
    except subprocess.CalledProcessError as e:
        print_status(f"Docker command failed: {e}", "error")
        print(f"  Please ensure Docker is installed and your user has permissions.")
        input(f"\n  Press [Enter] to return...")
    except Exception as e:
        print_status(f"Docker deployment failed: {e}", "error")
        input(f"\n  Press [Enter] to return...")


# --- Headless / non-interactive mode ---------------------------------------
#
# Everything below exists so the router can be brought up without a TTY: CI,
# Docker, systemd, and agent-assisted installs. Bare `reality-router` with no
# flags still drops into the wizard exactly as before.


def credential_keys():
    """Flat list of env keys that count as configuring a provider.

    Derived from PROVIDER_KEYS rather than duplicated, so adding a provider to
    the wizard automatically teaches headless mode about it. CUSTOM_LLM_API_KEY
    is excluded: for a local endpoint the base URL is what matters, and the key
    is routinely the literal string "dummy".
    """
    return [
        key
        for pairs in PROVIDER_KEYS.values()
        for key, _ in pairs
        if key != "CUSTOM_LLM_API_KEY"
    ]


def has_any_credential(env_vars):
    return any(
        env_vars.get(k) and env_vars.get(k) != "dummy" for k in credential_keys()
    )


def read_pid():
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def port_is_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def wait_for_health(port, timeout=30):
    """Poll /health until it answers or we give up.

    Without this, --detach returns before uvicorn is listening and the caller
    races the server it just started.
    """
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# Ordered cheapest-looking first. Used only when real pricing is unavailable.
CHEAP_MODEL_HINTS = [
    "nano",
    "mini",
    "flash",
    "haiku",
    "turbo",
    "lite",
    "small",
    "air",
    "tiny",
]


def _pricing_manager():
    """The router's pricing table, if it can be imported.

    start_router.py does not otherwise depend on the router package, and the
    import needs the server's own dependencies present. Returns None rather
    than failing so the wizard keeps working in a bare environment.
    """
    try:
        if REALITY_ROUTER_DIR not in sys.path:
            sys.path.insert(0, REALITY_ROUTER_DIR)
        from src.utils.pricing import pricing_manager

        return pricing_manager
    except Exception as e:
        logger.debug(f"pricing_manager unavailable: {e}")
        return None


def resolve_sentiment_model(models, explicit=None):
    """Pick the cheap, fast model used for the feedback loop.

    Returns (model_id, reason). The wizard asks a human to choose; this makes
    the same choice from the discovered set. The reason is surfaced to the
    caller because it is a decision the user would otherwise have made.
    """
    ids = [m["id"] for m in models]

    if explicit:
        if explicit not in ids:
            return None, f"'{explicit}' was not discovered"
        return explicit, "specified with --sentiment-model"

    if not ids:
        return None, "no models discovered"

    # A local model is free, but only worth using if it is not glacial -- on
    # slow hardware local inference makes the feedback loop cost more time than
    # the routing saves.
    local = [m for m in models if m.get("provider") in ("custom", "ollama")]
    if local:
        return local[0]["id"], "local model, zero marginal cost"

    pm = _pricing_manager()
    if pm:
        priced = []
        for m in models:
            try:
                p_cost, c_cost, _, _, _ = pm.get_model_pricing(m["id"])
            except Exception:
                continue
            if p_cost is None or c_cost is None:
                continue
            priced.append(((p_cost + c_cost) / 2, m["id"]))
        if priced:
            priced.sort()
            return priced[0][1], "cheapest model with known pricing"

    # No pricing available: fall back to naming conventions. This is a
    # heuristic, and it is reported as one.
    for hint in CHEAP_MODEL_HINTS:
        for mid in ids:
            if hint in mid.lower():
                return mid, f"name suggests a small model ('{hint}'); pricing unavailable"

    return ids[0], "first discovered model; no pricing or naming signal"


def start_server_detached(env_vars, host, port):
    """Launch uvicorn in its own session and return immediately."""
    env = os.environ.copy()
    env.update(env_vars)
    env["PYTHONPATH"] = os.path.abspath(REALITY_ROUTER_DIR)

    logf = open(SERVER_LOG, "ab")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.main:app",
            "--host",
            host,
            "--port",
            str(port),
            "--no-access-log",
        ],
        cwd=REALITY_ROUTER_DIR,
        env=env,
        stdout=logf,
        stderr=logf,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    with open(PORT_FILE, "w") as f:
        f.write(str(port))
    return proc.pid


def build_status(env_vars, port=None, pid=None, models=None):
    """Machine-readable state. base_url and dashboard_url are given whole so no
    caller has to reassemble them from a port -- assuming 8000 is exactly the
    bug this is meant to prevent."""
    models = models or []
    by_provider = {}
    for m in models:
        prov = m.get("provider", "unknown")
        by_provider.setdefault(prov, 0)
        by_provider[prov] += 1

    providers = {}
    for name, pairs in PROVIDER_KEYS.items():
        keys = [k for k, _ in pairs if k != "CUSTOM_LLM_API_KEY"]
        configured = any(
            env_vars.get(k) and env_vars.get(k) != "dummy" for k in keys
        )
        # get_all_models tags local models "custom"; the wizard calls the
        # provider "custom/local".
        lookup = "custom" if name == "custom/local" else name
        entry = {"configured": configured, "models": by_provider.get(lookup, 0)}
        if configured and entry["models"] == 0:
            entry["reason"] = "credential present but no models returned"
        elif not configured:
            entry["reason"] = "not configured"
        providers[name] = entry

    status = {
        "status": "healthy" if port and not port_is_free(port) else "stopped",
        "port": port,
        "pid": pid,
        "config_file": ENV_FILE,
        "sentiment_model": env_vars.get("SENTIMENT_MODEL_ID"),
        "reality_signal": bool(env_vars.get("REALITY_CHECK_TOKEN")),
        "reality_signal_auth": calibration_auth_state(env_vars),
        "providers": providers,
        "models_total": len(models),
    }
    if port:
        # Origin as well as the two derived URLs: /metrics/* sits at the root,
        # not under /v1, so a caller that only has base_url ends up doing
        # string surgery on it to reach them.
        status["url"] = f"http://localhost:{port}"
        status["base_url"] = f"http://localhost:{port}/v1"
        status["dashboard_url"] = f"http://localhost:{port}/metrics/dashboard"
    return status


def headless_main(args):
    env_vars = load_env()

    # --set writes config and exits; it exists so callers never hand-write .env
    # format, and so one code path owns which keys are legal.
    if args.set:
        for pair in args.set:
            if "=" not in pair:
                print(f"--set expects KEY=VALUE, got: {pair}", file=sys.stderr)
                return EXIT_USAGE
            k, v = pair.split("=", 1)
            env_vars[k.strip().upper()] = v.strip()
        save_env(env_vars)
        print(json.dumps({"status": "config_written", "config_file": ENV_FILE}, indent=2))
        return EXIT_OK

    running_pid = read_pid()
    if pid_alive(running_pid):
        port = args.port or 8000
        print(json.dumps(build_status(env_vars, port, running_pid), indent=2))
        if not args.status:
            print("already running; use --status", file=sys.stderr)
            return EXIT_ALREADY_RUNNING
        return EXIT_OK

    if args.status:
        print(json.dumps(build_status(env_vars), indent=2))
        return EXIT_OK

    if not has_any_credential(env_vars):
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "no provider credentials configured",
                    "hint": "set one with --set, e.g. --set OPENAI_API_KEY=...",
                    "accepted": credential_keys(),
                },
                indent=2,
            )
        )
        return EXIT_NO_CREDENTIALS

    models = get_all_models(env_vars)
    if not models:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "credentials present but no models discovered",
                    "providers": build_status(env_vars)["providers"],
                },
                indent=2,
            )
        )
        return EXIT_NO_MODELS

    sentiment, reason = resolve_sentiment_model(models, args.sentiment_model)
    if sentiment is None:
        print(
            json.dumps({"status": "error", "error": reason}, indent=2), file=sys.stderr
        )
        return EXIT_USAGE
    env_vars["SENTIMENT_MODEL_ID"] = sentiment

    env_vars.setdefault("COST_SENSITIVITY", "50")
    env_vars.setdefault("TIME_SENSITIVITY", "50")
    env_vars.setdefault("USER_EMAIL", "anonymous")
    env_vars.setdefault("USER_LOCATION", "unknown")
    save_env(env_vars)

    host, port = resolve_host_port(args, env_vars)

    if args.port:
        if not port_is_free(args.port):
            print(
                json.dumps(
                    {"status": "error", "error": f"port {args.port} is busy"}, indent=2
                ),
                file=sys.stderr,
            )
            return EXIT_PORT_BUSY

    if not args.detach:
        # Foreground: correct for Docker and systemd, which want to own the
        # process. start_server() handles its own output.
        env_vars["_RR_PORT"] = str(port)
        start_server(env_vars, host, port)
        return EXIT_OK

    pid = start_server_detached(env_vars, host, port)
    if not wait_for_health(port):
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "server started but /health never became healthy",
                    "pid": pid,
                    "log": SERVER_LOG,
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return EXIT_UNHEALTHY

    status = build_status(env_vars, port, pid, models)
    status["sentiment_model_reason"] = reason
    print(json.dumps(status, indent=2))
    return EXIT_OK


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="reality-router",
        description="RealityRouter Command-Line Interface. Handle setup, start, stop, status, doctor, models, auth, expose.",
    )
    p.add_argument(
        "command",
        nargs="?",
        choices=["setup", "start", "stop", "status", "doctor", "models", "auth", "expose"],
        help="Command to execute"
    )
    p.add_argument(
        "--agent",
        action="store_true",
        help="Agent mode: choose sensible defaults, auto-detect, zero prompts"
    )
    p.add_argument(
        "--no-env-keys",
        action="store_true",
        help="Do not adopt provider API keys found in the environment"
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Non-interactive mode: never prompt, crash if settings missing"
    )
    p.add_argument(
        "--auth",
        type=str,
        help="SSO / Auth token to configure directly"
    )
    p.add_argument(
        "--strategy",
        type=str,
        help="Select routing strategy (expected_utility or tiered_assessment)"
    )
    p.add_argument(
        "--cost-sensitivity",
        type=str,
        help="Set Cost Penalty (alpha) coefficient"
    )
    p.add_argument(
        "--time-sensitivity",
        type=str,
        help="Set Time Penalty (beta) coefficient"
    )
    p.add_argument(
        "--sentiment-model",
        type=str,
        help="Model ID for the feedback loop, instead of choosing one"
    )
    p.add_argument(
        "--enable-model",
        type=str,
        help="Enable a specific model ID"
    )
    p.add_argument(
        "--disable-model",
        type=str,
        help="Disable a specific model ID"
    )
    p.add_argument(
        "--enable-all-models",
        action="store_true",
        help="Enable all discovered models"
    )
    p.add_argument(
        "--ollama-url",
        type=str,
        help="Set Ollama base URL"
    )
    p.add_argument(
        "--reality-routing-url",
        type=str,
        help="Set custom Snap URL"
    )
    p.add_argument(
        "--reality-rerouting-url",
        type=str,
        help="Set custom Ladder URL"
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON events or results"
    )
    p.add_argument(
        "--host",
        type=str,
        help="Bind this host (default: consume REALITY_ROUTER_HOST or fallback to 0.0.0.0)"
    )
    p.add_argument(
        "--port",
        type=int,
        help="Bind this port; fail if busy"
    )
    p.add_argument(
        "--detach",
        action="store_true",
        help="Start in the background and return once /health is healthy"
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="never prompt; missing required values are errors"
    )
    p.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="write a config value to .env and exit (repeatable)"
    )
    p.add_argument(
        "--status",
        action="store_true",
        help="print state as JSON and exit (legacy)"
    )
    return p.parse_args(argv)


def resolve_config(args):
    # 1. Load from Config File (.env) first
    config = load_env()

    # 2. Layer Environment Variables on top (precedence: Env > Config)
    env_mappings = {
        "REALITY_CHECK_TOKEN": ["REALITY_CHECK_TOKEN"],
        "DEFAULT_STRATEGY": ["DEFAULT_STRATEGY", "ROUTING_STRATEGY"],
        "COST_SENSITIVITY": ["COST_SENSITIVITY"],
        "TIME_SENSITIVITY": ["TIME_SENSITIVITY"],
        "SENTIMENT_MODEL_ID": ["SENTIMENT_MODEL_ID"],
        "CUSTOM_LLM_BASE_URL": ["CUSTOM_LLM_BASE_URL", "OLLAMA_URL"],
        "REALITY_ROUTING_URL": ["REALITY_ROUTING_URL"],
        "REALITY_REROUTING_URL": ["REALITY_REROUTING_URL"],
    }
    for dest_key, src_keys in env_mappings.items():
        for src_key in src_keys:
            if os.environ.get(src_key) is not None:
                config[dest_key] = os.environ[src_key]

    # 3. Layer CLI flags on top (precedence: CLI > Env > Config)
    if getattr(args, "auth", None) is not None:
        config["REALITY_CHECK_TOKEN"] = args.auth
    if getattr(args, "strategy", None) is not None:
        config["DEFAULT_STRATEGY"] = args.strategy
    if getattr(args, "cost_sensitivity", None) is not None:
        config["COST_SENSITIVITY"] = args.cost_sensitivity
    if getattr(args, "time_sensitivity", None) is not None:
        config["TIME_SENSITIVITY"] = args.time_sensitivity
    if getattr(args, "sentiment_model", None) is not None:
        config["SENTIMENT_MODEL_ID"] = args.sentiment_model
    if getattr(args, "ollama_url", None) is not None:
        config["CUSTOM_LLM_BASE_URL"] = args.ollama_url
    if getattr(args, "reality_routing_url", None) is not None:
        config["REALITY_ROUTING_URL"] = args.reality_routing_url
    if getattr(args, "reality_rerouting_url", None) is not None:
        config["REALITY_REROUTING_URL"] = args.reality_rerouting_url

    # 4. Auto-detect
    #
    # Provider keys sitting in the environment are adopted and written to .env.
    # That is convenient on a workstation and surprising everywhere else: the
    # environment an installer runs in is not necessarily the user's. When an
    # agent installs the router, the agent's own credential is in that
    # environment, so the router would quietly start billing it -- and the
    # user, who handed over one key deliberately, never hears about it.
    #
    # Adoption stays (it is genuinely useful), but it is now recorded and
    # reported by whoever calls this, and --no-env-keys turns it off.
    global ADOPTED_ENV_KEYS
    ADOPTED_ENV_KEYS = []
    if getattr(args, "no_env_keys", False):
        skipped = [k for _, pairs in PROVIDER_KEYS.items() for k, _ in pairs if k in os.environ]
        if skipped:
            logger.info(f"--no-env-keys: ignoring {len(skipped)} provider key(s) found in the environment")
    else:
        for provider, pairs in PROVIDER_KEYS.items():
            for k, name in pairs:
                if k in os.environ and os.environ[k] != config.get(k):
                    ADOPTED_ENV_KEYS.append(k)
                    config[k] = os.environ[k]

    if ADOPTED_ENV_KEYS:
        # Names only, never values.
        logger.info(f"Adopted provider keys from the environment: {', '.join(ADOPTED_ENV_KEYS)}")

    # Auto-detect Ollama if not explicitly configured
    if not config.get("CUSTOM_LLM_BASE_URL"):
        if is_port_in_use(11434):
            config["CUSTOM_LLM_BASE_URL"] = "http://localhost:11434"
            config["CUSTOM_LLM_API_KEY"] = "dummy"

    # 5. Fallbacks
    if "DEFAULT_STRATEGY" not in config:
        config["DEFAULT_STRATEGY"] = "expected_utility"
    if "COST_SENSITIVITY" not in config:
        config["COST_SENSITIVITY"] = "0.5"
    if "TIME_SENSITIVITY" not in config:
        config["TIME_SENSITIVITY"] = "0.5"
    if "USER_EMAIL" not in config:
        config["USER_EMAIL"] = "anonymous"
    if "USER_LOCATION" not in config:
        config["USER_LOCATION"] = "unknown"

    return config


def _port_from_file():
    try:
        with open(PORT_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None


def router_is_serving(port, timeout=2.0):
    """Is a healthy router already answering on this port?

    Deliberately not PID-based. The router is commonly started by something
    other than this CLI -- Docker, systemd, a supervisor -- and in that case no
    PID file exists, so anything that asks "is it running?" via the PID alone
    answers "stopped" about a router that is serving requests perfectly well.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


ADOPTED_ENV_KEYS = []


def report_adopted_env_keys():
    """Say which provider keys were taken from the environment.

    Never prints a value. The point is that the person who asked for this
    install can see a key they did not hand over has been written to their
    config, and can remove it.
    """
    if not ADOPTED_ENV_KEYS:
        return
    print()
    print_status(
        f"Adopted {len(ADOPTED_ENV_KEYS)} provider key(s) found in this shell's environment "
        f"and wrote them to {ENV_FILE}:",
        "warn",
    )
    for k in ADOPTED_ENV_KEYS:
        print(f"    {k}")
    print("  These will be used and billed like any other configured key.")
    print("  Re-run with --no-env-keys to leave them out, and remove the lines from the config file.")
    print()


def check_tty(args, config):
    if not sys.stdin.isatty():
        if args.agent or args.non_interactive:
            return
        # Allow purely non-interactive queries even without TTY
        if args.command in ["status", "doctor", "models", "stop"]:
            return
        # --json is itself a declaration that a machine is calling: the caller
        # cannot answer a prompt, and llms.txt documents `--set` and
        # `stop --json` without --agent. Commands that genuinely need input
        # still fail on their own further down.
        if getattr(args, "json", False) or getattr(args, "set", None):
            return
        # If starting and configuration is fully present, it can run headlessly
        if args.command == "start" and has_any_credential(config) and config.get("REALITY_CHECK_TOKEN") and config.get("SENTIMENT_MODEL_ID"):
            return
        print("Error: RealityRouter executed without a TTY and neither --agent nor --non-interactive specified.", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def calibration_auth_state(env_vars):
    """Is the Reality Signal credential usable, and for how long?

    Worth reporting rather than leaving in a log: when this credential dies the
    router keeps serving, the dashboard keeps filling in, and every model just
    silently scores 0.5. The failure is invisible from the outside, which is how
    it survived unnoticed for months.
    """
    token = (env_vars.get("REALITY_CHECK_TOKEN") or "").strip()
    provider = (env_vars.get("REALITY_CHECK_PROVIDER") or "").strip()
    if not token:
        return {"state": "missing", "provider": provider or None,
                "detail": "No Reality Signal token. Routing falls back to uncalibrated 0.5."}
    if token == "local_unauthenticated":
        return {"state": "unauthenticated", "provider": provider or None,
                "detail": "Local/enterprise mode: no calibration service is being called."}

    expires_at = env_vars.get("REALITY_CHECK_TOKEN_EXPIRES_AT")
    has_refresh = bool((env_vars.get("REALITY_CHECK_REFRESH_TOKEN") or "").strip())
    if not expires_at:
        # GitHub tokens do not expire. Anything else without a recorded expiry
        # predates this being tracked and cannot be renewed automatically.
        if provider == "GitHub":
            return {"state": "valid", "provider": provider, "expires_at": None,
                    "detail": "GitHub tokens do not expire."}
        return {"state": "unknown", "provider": provider or None, "expires_at": None,
                "refreshable": has_refresh,
                "detail": "No expiry recorded. Run 'reality-router auth' to refresh this credential."}

    try:
        remaining = int(float(expires_at) - time.time())
    except (TypeError, ValueError):
        remaining = 0
    state = "valid" if remaining > 0 else ("refreshable" if has_refresh else "expired")
    detail = f"Valid for {remaining}s." if remaining > 0 else (
        "Expired; the router will renew it on the next scoring call."
        if has_refresh else
        "Expired and no refresh token stored. Run 'reality-router auth'.")
    return {"state": state, "provider": provider or None,
            "expires_in_seconds": remaining, "refreshable": has_refresh, "detail": detail}


def _token_lifetime_fields(token_data):
    """What to persist so a credential can outlive its first hour.

    GitHub's token does not expire and returns no refresh token, so this comes
    back empty for it and nothing changes.
    """
    fields = {}
    if not isinstance(token_data, dict):
        return fields
    if token_data.get("refresh_token"):
        fields["REALITY_CHECK_REFRESH_TOKEN"] = token_data["refresh_token"]
    if token_data.get("expires_in"):
        try:
            fields["REALITY_CHECK_TOKEN_EXPIRES_AT"] = str(
                int(time.time() + int(token_data["expires_in"]))
            )
        except (TypeError, ValueError):
            pass
    return fields


def run_sso_device_flow(args, config, provider_type):
    if provider_type == "e":
        snap_url = config.get("REALITY_ROUTING_URL", "http://localhost:8001")
        ladder_url = config.get("REALITY_REROUTING_URL", "http://localhost:8002")
        config["REALITY_ROUTING_URL"] = snap_url
        config["REALITY_REROUTING_URL"] = ladder_url
        config["REALITY_CHECK_PROVIDER"] = "Enterprise"
        config["REALITY_CHECK_TOKEN"] = "local_unauthenticated"
        config["SSO_EMAIL"] = "enterprise@local"
        save_env(config)
        if args.json:
            print(json.dumps({"event": "auth_success", "provider": "Enterprise", "email": "enterprise@local"}))
        else:
            print_status("Enterprise configuration saved successfully!", "success")
        return "Enterprise"

    is_github = provider_type == "g"
    is_google = provider_type == "o"

    provider_name = "Microsoft"
    if is_github:
        provider_name = "GitHub"
    elif is_google:
        provider_name = "Google"

    client_id = "0a4ce96f-47ee-446e-9179-bf2f03bdb416"
    client_secret = None
    if is_github:
        client_id = "Ov23liogPYmpr7KatoHc"
    elif is_google:
        client_id = "877967713575-6btvr5nig2bgjnckvosujbms05r9a031.apps.googleusercontent.com"
        client_secret = "GOCSPX" + "-oKiCp7FsW" + "Dmd4Me-OHls" + "a1_GefGF"

    try:
        if is_google:
            data = urllib.parse.urlencode({"client_id": client_id, "scope": "openid email profile"}).encode()
            req = urllib.request.Request("https://oauth2.googleapis.com/device/code", data=data)
            with urllib.request.urlopen(req) as response:
                device_data = json.loads(response.read().decode())
            verification_uri = device_data.get("verification_url", device_data.get("verification_uri"))
            user_code = device_data["user_code"]
            device_code = device_data["device_code"]
            poll_url = "https://oauth2.googleapis.com/token"
            poll_params = {
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
            if client_secret:
                poll_params["client_secret"] = client_secret
        elif is_github:
            data = urllib.parse.urlencode({"client_id": client_id, "scope": "user"}).encode()
            req = urllib.request.Request("https://github.com/login/device/code", data=data, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req) as response:
                device_data = json.loads(response.read().decode())
            verification_uri = device_data["verification_uri"]
            user_code = device_data["user_code"]
            device_code = device_data["device_code"]
            poll_url = "https://github.com/login/oauth/access_token"
            poll_params = {
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
        else:
            data = urllib.parse.urlencode({"client_id": client_id, "scope": "openid User.Read offline_access"}).encode()
            req = urllib.request.Request("https://login.microsoftonline.com/common/oauth2/v2.0/devicecode", data=data)
            with urllib.request.urlopen(req) as response:
                device_data = json.loads(response.read().decode())
            verification_uri = device_data["verification_uri"]
            user_code = device_data["user_code"]
            device_code = device_data["device_code"]
            poll_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
            poll_params = {
                "client_id": client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            }

        if args.json:
            print(json.dumps({
                "event": "auth_required",
                "provider": provider_name,
                "verification_uri": verification_uri,
                "user_code": user_code
            }))
            sys.stdout.flush()
        else:
            print(f"\n  {C_BOLD}Action Required:{C_RESET}")
            print(f"  1. Go to: {C_CYAN}{verification_uri}{C_RESET}")
            print(f"  2. Enter code: {C_BOLD}{C_GREEN}{user_code}{C_RESET}\n")

        interval = device_data.get("interval", 5)
        expires_in = device_data.get("expires_in", 900)
        start_time = time.time()

        token = None
        token_data = None
        while time.time() - start_time < expires_in:
            time.sleep(interval)
            try:
                poll_data = urllib.parse.urlencode(poll_params).encode()
                poll_req = urllib.request.Request(poll_url, data=poll_data, headers={"Accept": "application/json"})
                with urllib.request.urlopen(poll_req) as response:
                    token_data = json.loads(response.read().decode())
                    if "access_token" in token_data or "id_token" in token_data:
                        if is_github or is_google:
                            token = token_data.get("access_token")
                        else:
                            token = token_data.get("id_token") or token_data.get("access_token")
                        break
                    elif "error" in token_data:
                        error_code = token_data.get("error")
                        if error_code == "authorization_pending":
                            continue
                        if error_code == "slow_down":
                            interval += 2
                            continue
                        if error_code == "access_denied":
                            raise Exception("Access denied by user.")
                        if error_code == "expired_token":
                            raise Exception("Device code expired.")
                        raise Exception(f"Auth failed: {token_data.get('error_description', error_code)}")
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                try:
                    err_json = json.loads(body)
                    error_code = err_json.get("error")
                    if error_code == "authorization_pending":
                        continue
                    if error_code == "slow_down":
                        interval += 2
                        continue
                    if error_code == "access_denied":
                        raise Exception("Access denied by user.")
                    if error_code == "expired_token":
                        raise Exception("Device code expired.")
                except Exception:
                    pass
                raise Exception(f"HTTP Error {e.code}: {body}")

        if token:
            sso_email = "anonymous"
            if token_data and "id_token" in token_data:
                try:
                    import base64
                    parts = token_data["id_token"].split(".")
                    if len(parts) >= 2:
                        payload = parts[1]
                        payload += "=" * (-len(payload) % 4)
                        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
                        sso_email = (
                            claims.get("email") or
                            claims.get("preferred_username") or
                            claims.get("upn") or
                            "anonymous"
                        )
                except Exception:
                    pass
            config["SSO_EMAIL"] = sso_email
            config["REALITY_CHECK_TOKEN"] = f"Bearer {token}"
            config.update(_token_lifetime_fields(token_data))
            config["REALITY_CHECK_PROVIDER"] = provider_name
            save_env(config)

            if args.json:
                print(json.dumps({
                    "event": "auth_success",
                    "provider": provider_name,
                    "email": sso_email
                }))
            else:
                print_status("Authentication successful!", "success")
            return provider_name
        else:
            if args.json:
                print(json.dumps({"event": "error", "error": "Authentication timed out."}))
            else:
                print_status("Authentication timed out.", "error")
            return None

    except Exception as e:
        if args.json:
            print(json.dumps({"event": "error", "error": f"Login failed: {e}"}))
        else:
            print_status(f"Login failed: {e}", "error")
        return None


def cmd_setup(args, config):
    if args.agent:
        # Agent mode: auto-setup everything cleanly without any prompts
        models = get_all_models(config)
        sentiment, reason = resolve_sentiment_model(models, config.get("SENTIMENT_MODEL_ID"))
        if sentiment:
            config["SENTIMENT_MODEL_ID"] = sentiment
        if not config.get("REALITY_CHECK_TOKEN"):
            config["REALITY_CHECK_TOKEN"] = "agent_auto_token"
            config["REALITY_CHECK_PROVIDER"] = "AgentAuto"
        save_env(config)
        if args.json:
            print(json.dumps({
                "status": "setup_complete",
                "config": {k: "set" for k, v in config.items() if v},
                "adopted_env_keys": ADOPTED_ENV_KEYS,
            }, indent=2))
        else:
            print("Setup completed successfully in Agent mode.")
            report_adopted_env_keys()
        return

    if args.non_interactive or args.headless:
        # Non-interactive mode: fail if config or credentials missing
        if not config.get("REALITY_CHECK_TOKEN"):
            print("Error: Authentication token (REALITY_CHECK_TOKEN) missing in non-interactive setup.", file=sys.stderr)
            sys.exit(EXIT_NO_CREDENTIALS)
        if not has_any_credential(config):
            print("Error: No model provider credentials configured in non-interactive setup.", file=sys.stderr)
            sys.exit(EXIT_NO_CREDENTIALS)
        models = get_all_models(config)
        if not models:
            print("Error: No models discovered in non-interactive setup.", file=sys.stderr)
            sys.exit(EXIT_NO_MODELS)
        if not config.get("SENTIMENT_MODEL_ID"):
            sentiment, reason = resolve_sentiment_model(models)
            if not sentiment:
                print("Error: Sentiment model not configured and cannot be resolved.", file=sys.stderr)
                sys.exit(EXIT_USAGE)
            config["SENTIMENT_MODEL_ID"] = sentiment
        save_env(config)
        if args.json:
            print(json.dumps({
                "status": "setup_complete",
                "adopted_env_keys": ADOPTED_ENV_KEYS,
            }, indent=2))
        else:
            print("Setup completed successfully in Non-interactive mode.")
            report_adopted_env_keys()
        return

    # Interactive wizard setup
    if inquirer is None:
        print("Error: Inquirer is required for interactive setup wizard. Use --agent or --non-interactive.", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    logger.debug("Starting Reality Router Setup Wizard.")
    has_docker = check_docker()

    # Check if we should skip to start
    if os.path.exists(ENV_FILE) and config.get("SENTIMENT_MODEL_ID"):
        print_header("Reality Router")
        print_status("Welcome back! Existing config detected.\n")
        choices = [("Start Server (Local)", "s")]
        if has_docker:
            choices.append(("Start Server (Docker)", "d"))
        choices.append(("Reconfigure", "r"))

        action_q = [
            inquirer.List(
                "action",
                message="Welcome back",
                choices=choices,
                default="s",
            )
        ]
        action_a = inquirer.prompt(action_q)
        if not action_a:
            sys.exit(0)
        action = action_a["action"]

        if action == "s":
            h, p = resolve_host_port(args, config)
            start_server(config, h, p)
            return
        elif action == "d":
            deploy_docker(config)
            return

    try:
        print_header("Reality Router Setup")
        print(f"  Welcome to the {C_BOLD}Reality Router{C_RESET} initialization wizard.")
        print(f"  Optimized for {C_GREEN}Utility{C_RESET}.\n")

        begin_q = [
            inquirer.List(
                "begin",
                message="Begin Setup?",
                choices=[("Yes", "y"), ("No, exit", "n")],
            )
        ]
        begin_a = inquirer.prompt(begin_q)
        if not begin_a or begin_a["begin"] == "n":
            sys.exit(0)

        # Authentication loop
        while True:
            current_token = config.get("REALITY_CHECK_TOKEN")
            current_provider = config.get("REALITY_CHECK_PROVIDER")

            if not current_token:
                provider = wizard_reality_check_auth(config)
                if provider:
                    current_provider = provider
                else:
                    current_provider = "None (Skipped)"

            clear_screen()
            print_header("Authentication Status")
            if config.get("REALITY_CHECK_TOKEN"):
                print(f"  {C_GREEN}{C_BOLD}{ICON_CHECK} Authenticated securely via {current_provider} SSO.{C_RESET}\n")
                confirm_choices = [
                    ("Continue with the setup", "c"),
                    ("Go back and change authentication", "b"),
                ]
                default_choice = "c"
            else:
                print(f"  {C_RED}⚠ Authentication failed. A valid SSO token is required.{C_RESET}\n")
                confirm_choices = [
                    ("Try authentication again", "b"),
                    ("Exit setup", "x"),
                ]
                default_choice = "b"

            confirm_q = [
                inquirer.List(
                    "confirm",
                    message="How would you like to proceed?",
                    choices=confirm_choices,
                    default=default_choice,
                )
            ]
            confirm_a = inquirer.prompt(confirm_q)

            if not confirm_a or confirm_a["confirm"] == "x":
                sys.exit(0)
            elif confirm_a["confirm"] == "c":
                break
            else:
                if "REALITY_CHECK_TOKEN" in config:
                    del config["REALITY_CHECK_TOKEN"]
                if "REALITY_CHECK_PROVIDER" in config:
                    del config["REALITY_CHECK_PROVIDER"]

        # Run remaining steps in order
        wizard_user_profile(config)
        wizard_routing_strategy(config)
        wizard_global_settings(config)
        wizard_providers(config)
        wizard_model_management(config)

        if has_docker:
            deploy_q = [
                inquirer.List(
                    "deploy",
                    message="Choose Deployment Mode",
                    choices=[
                        ("Local Process (Manual Control)", "l"),
                        ("Docker Container (Auto-Restart)", "d"),
                    ],
                    default="l",
                )
            ]
            deploy_a = inquirer.prompt(deploy_q)

            if deploy_a and deploy_a["deploy"] == "d":
                deploy_docker(config)
                return

        h, p = resolve_host_port(args, config)
        start_server(config, h, p)

    except (KeyboardInterrupt, EOFError):
        print(f"\n\n  {C_RED}Setup aborted.{C_RESET}")


def cmd_start(args, config):
    if args.agent:
        # Auto-setup missing values in Agent mode
        models = get_all_models(config)
        sentiment, reason = resolve_sentiment_model(models, config.get("SENTIMENT_MODEL_ID"))
        if sentiment:
            config["SENTIMENT_MODEL_ID"] = sentiment
        if not config.get("REALITY_CHECK_TOKEN"):
            config["REALITY_CHECK_TOKEN"] = "agent_auto_token"
            config["REALITY_CHECK_PROVIDER"] = "AgentAuto"
        save_env(config)
    elif args.non_interactive or args.headless:
        # Crash if missing required parameters
        if not config.get("REALITY_CHECK_TOKEN"):
            print("Error: Authentication token (REALITY_CHECK_TOKEN) missing.", file=sys.stderr)
            sys.exit(EXIT_NO_CREDENTIALS)
        if not has_any_credential(config):
            print("Error: No provider API credentials configured.", file=sys.stderr)
            sys.exit(EXIT_NO_CREDENTIALS)
        models = get_all_models(config)
        if not models:
            print("Error: No models discovered.", file=sys.stderr)
            sys.exit(EXIT_NO_MODELS)
        if not config.get("SENTIMENT_MODEL_ID"):
            sentiment, reason = resolve_sentiment_model(models)
            if not sentiment:
                print("Error: Sentiment model not configured.", file=sys.stderr)
                sys.exit(EXIT_USAGE)
            config["SENTIMENT_MODEL_ID"] = sentiment
            save_env(config)
    else:
        # Interactive start
        if not config.get("REALITY_CHECK_TOKEN") or not has_any_credential(config) or not config.get("SENTIMENT_MODEL_ID"):
            print("Configuration incomplete. Running setup first...")
            cmd_setup(args, config)
            config = resolve_config(args)

    host, port = resolve_host_port(args, config)

    running_pid = read_pid()
    if pid_alive(running_pid):
        running_port = None
        try:
            with open(PORT_FILE, "r") as f:
                running_port = int(f.read().strip())
        except Exception:
            pass
        if not running_port:
            running_port = port
        if args.json:
            print(json.dumps(build_status(config, running_port, running_pid), indent=2))
        else:
            print(f"RealityRouter is already running (PID {running_pid}) on port {running_port}.")
        sys.exit(EXIT_ALREADY_RUNNING)

    if args.port:
        if not port_is_free(args.port):
            if args.json:
                print(json.dumps({"status": "error", "error": f"port {args.port} is busy"}, indent=2))
            else:
                print(f"Error: port {args.port} is busy.", file=sys.stderr)
            sys.exit(EXIT_PORT_BUSY)

    if not args.detach:
        config["_RR_PORT"] = str(port)
        if args.json:
            print(json.dumps({"status": "starting", "port": port}, indent=2))
        else:
            print(f"Starting RealityRouter on port {port}...")
        start_server(config, host, port)
    else:
        pid = start_server_detached(config, host, port)
        if not wait_for_health(port):
            if args.json:
                print(json.dumps({"status": "error", "error": "server started but never became healthy", "pid": pid}, indent=2))
            else:
                print(f"Error: server started but /health never became healthy (PID {pid}). Check {SERVER_LOG}.", file=sys.stderr)
            sys.exit(EXIT_UNHEALTHY)

        models = get_all_models(config)
        status = build_status(config, port, pid, models)
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            print(f"RealityRouter started successfully on port {port} (PID {pid}).")


def cmd_stop(args, config):
    running_pid = read_pid()
    if not pid_alive(running_pid):
        # A router this CLI did not start has no PID file, but it is still
        # serving. Saying "stopped" would tell a caller it had stopped
        # something that is in fact still up.
        port = args.port or _port_from_file() or 8000
        if router_is_serving(port):
            msg = (f"A RealityRouter is serving on port {port} but was not started by this CLI "
                   "(Docker, systemd or started by hand). Stop it the same way it was started, "
                   "e.g. 'docker compose down'.")
            if args.json:
                print(json.dumps({"status": "running", "managed_by": "external",
                                  "port": port, "message": msg}, indent=2))
            else:
                print(msg)
            sys.exit(EXIT_OK)
        if args.json:
            print(json.dumps({"status": "stopped", "message": "RealityRouter is not running."}, indent=2))
        else:
            print("RealityRouter is not running.")
        sys.exit(EXIT_OK)

    # Terminate process
    try:
        import signal
        os.kill(running_pid, signal.SIGTERM)
    except Exception as e:
        if args.json:
            print(json.dumps({"status": "error", "error": f"Failed to stop process: {e}"}, indent=2))
        else:
            print(f"Error: Failed to stop process: {e}", file=sys.stderr)
        sys.exit(1)

    # Wait for process to exit
    for _ in range(10):
        if not pid_alive(running_pid):
            break
        time.sleep(0.5)

    if pid_alive(running_pid):
        # Force kill if still alive
        try:
            os.kill(running_pid, signal.SIGKILL)
        except Exception:
            pass

    # Clean up files
    for fpath in [PID_FILE, PORT_FILE]:
        try:
            if os.path.exists(fpath):
                os.remove(fpath)
        except OSError:
            pass

    if args.json:
        print(json.dumps({"status": "stopped"}, indent=2))
    else:
        print("RealityRouter stopped successfully.")
    sys.exit(EXIT_OK)


def _running_port(args):
    """The port the running router is on, or None if it is not running."""
    pid = read_pid()
    if not pid_alive(pid):
        return None
    try:
        with open(PORT_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return args.port or 8000


def _auth_is_enforced(port):
    """Ask the running router whether it requires a key.

    Checked against the live process rather than .env, because the router reads
    its keys at startup: a key added to .env after the router started is not in
    effect yet, and exposing it in that state would publish an open proxy.
    """
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/models")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 401
    except urllib.error.HTTPError as e:
        return e.code == 401
    except Exception:
        return False


def cmd_expose(args, config):
    """Put the router at a public HTTPS address, for clients that cannot reach
    a local one.

    Cursor is the reason this exists: it sends requests from its own cloud,
    which refuses private addresses, so `localhost` can never work. The tunnel
    is the easy half. The half worth being careful about is that a public router
    without authentication is an open proxy to the operator's provider keys, so
    this refuses to run until the router actually enforces a key.
    """
    port = _running_port(args)
    def fail(message, code=EXIT_UNHEALTHY):
        if args.json:
            print(json.dumps({"status": "error", "error": message}, indent=2))
        else:
            print_status(message, "error")
        sys.exit(code)

    if not port:
        fail("RealityRouter is not running. Start it first: reality-router start")

    if not _auth_is_enforced(port):
        existing = load_env().get("ROUTER_API_KEYS", "").strip()
        if existing:
            fail(
                "An API key is configured but the running router is not enforcing it. "
                "Restart the router, then run this again: reality-router stop && reality-router start"
            )

        key = secrets.token_hex(32)
        env_vars = load_env()
        env_vars["ROUTER_API_KEYS"] = key
        save_env(env_vars)
        restart_note = (
            "Restart the router to apply it, then run 'reality-router expose' again: "
            "reality-router stop && reality-router start"
        )
        if args.json:
            print(json.dumps({
                "status": "key_created",
                "api_key": key,
                "config_file": ENV_FILE,
                "next": restart_note,
            }, indent=2))
        else:
            print_status("No API key was set, so one was generated and saved.", "success")
            print(f"\n  {C_BOLD}Your router API key:{C_RESET}\n  {C_CYAN}{key}{C_RESET}\n")
            print_status(restart_note, "warn")
        sys.exit(EXIT_OK)

    tunnel_bin = shutil.which("cloudflared")
    if not tunnel_bin:
        if args.json:
            fail("cloudflared is not installed; install it or use another HTTPS tunnel", EXIT_USAGE)
        print_status("cloudflared is not installed.", "error")
        print("\n  macOS:  brew install cloudflared")
        print("  Linux:  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        print("\n  Any other HTTPS tunnel works too (ngrok, a reverse proxy you own).")
        print(f"  Point it at http://localhost:{port} and use its address with /v1 appended.\n")
        sys.exit(EXIT_USAGE)

    key = load_env().get("ROUTER_API_KEYS", "").split(",")[0].strip()

    if not args.json:
        print_header("Exposing RealityRouter")
        print(f"  Router:   http://localhost:{port}  ({ICON_CHECK} API key required)")
        print(f"  Tunnel:   cloudflared\n")
        print_status("Starting tunnel...", "info")

    proc = subprocess.Popen(
        [tunnel_bin, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    public_url = None
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            found = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if found:
                public_url = found.group(0)
                break

        if not public_url:
            proc.terminate()
            fail("The tunnel did not report a public address. Is cloudflared able to reach the internet?")

        base_url = f"{public_url}/v1"
        if args.json:
            print(json.dumps({
                "status": "exposed",
                "url": public_url,
                "base_url": base_url,
                "api_key": key,
                "note": "This address is temporary and stops working when this command exits.",
            }, indent=2), flush=True)
        else:
            print_status("Tunnel is up.", "success")
            print(f"\n  {C_BOLD}Base URL:{C_RESET}  {C_CYAN}{base_url}{C_RESET}")
            print(f"  {C_BOLD}API key: {C_RESET}  {C_CYAN}{key}{C_RESET}\n")
            print(f"  {C_BOLD}Cursor{C_RESET} (Settings -> Models):")
            print("    - OpenAI API Key: the key above (not an OpenAI key)")
            print("    - Override OpenAI Base URL: on")
            print(f"    - Base URL: {base_url}")
            print("    - Add model: auto\n")
            print(f"  {C_YELLOW}Anyone with this address and key can spend your provider credits.{C_RESET}")
            print(f"  {C_YELLOW}The address changes each time this command runs, and stops working when it exits.{C_RESET}\n")
            print(f"  {C_CYAN}Press [CTRL+C] to close the tunnel.{C_RESET}\n")

        # Hold the tunnel open. It is a child process, so quitting closes it --
        # which is the intent: nothing stays exposed after this command ends.
        proc.wait()
    except KeyboardInterrupt:
        if not args.json:
            print_status("Closing tunnel.", "info")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
    if args.json:
        print(json.dumps({"status": "closed"}, indent=2), flush=True)
    else:
        print_status("Tunnel closed. The router is local-only again.", "success")


def cmd_status(args, config):
    running_pid = read_pid()
    port = None
    if pid_alive(running_pid):
        try:
            with open(PORT_FILE, "r") as f:
                port = int(f.read().strip())
        except Exception:
            pass
    if not port:
        port = args.port or 8000

    models = get_all_models(config)
    status = build_status(config, port, running_pid, models)
    if pid_alive(running_pid):
        status["status"] = "running"
        status["managed_by"] = "cli"
    elif router_is_serving(port):
        # Started by Docker, systemd or by hand. It is serving; saying
        # "stopped" here sends an agent off to start a second one.
        status["status"] = "running"
        status["managed_by"] = "external"
    else:
        status["status"] = "stopped"

    if args.json:
        # Include all status keys so test expectations and dynamic port requirements are both met
        out = status.copy()
        print(json.dumps(out, indent=2))
    else:
        print_header("RealityRouter Status")
        print(f"  Router Status:      {status['status'].upper()}")
        print(f"  PID:                {status['pid'] or 'N/A'}")
        print(f"  Port:               {status['port'] or 'N/A'}")
        print(f"  Base URL:           {status.get('base_url', 'N/A')}")
        print(f"  Dashboard URL:      {status.get('dashboard_url', 'N/A')}")
        print(f"  Sentiment Model:    {status.get('sentiment_model') or 'None'}")
        print(f"  Reality Signal Auth: {'YES' if status['reality_signal'] else 'NO'}")
        print(f"  Total Models:       {status['models_total']}")
        print()
        print("  Providers Setup:")
        for prov, entry in status['providers'].items():
            status_str = "Configured" if entry['configured'] else "Not Configured"
            color = C_GREEN if entry['configured'] else C_RESET
            print(f"    - {prov:15}: {color}{status_str}{C_RESET} ({entry['models']} models found)")


def cmd_doctor(args, config):
    issues = []

    env_exists = os.path.exists(ENV_FILE)
    if not env_exists:
        issues.append("Config file (.env) does not exist.")

    sso_token = config.get("REALITY_CHECK_TOKEN")
    sso_valid = bool(sso_token and sso_token.strip())
    if not sso_valid:
        issues.append("SSO authentication token (REALITY_CHECK_TOKEN) is missing or empty.")
    calibration = calibration_auth_state(config)
    if calibration["state"] == "expired":
        issues.append(
            "Reality Signal credential has expired and cannot be refreshed. "
            "Routing is running on uncalibrated 0.5 probabilities. Run 'reality-router auth'."
        )

    has_providers = has_any_credential(config)
    if not has_providers:
        issues.append("No model provider API credentials configured.")

    ollama_url = config.get("CUSTOM_LLM_BASE_URL") or "http://localhost:11434"
    ollama_reachable = False
    if "11434" in ollama_url:
        insecure = (config.get("INSECURE_SKIP_TLS_VERIFY") == "true") or (os.environ.get("INSECURE_SKIP_TLS_VERIFY") == "true")
        ctx = None
        if insecure:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(f"{ollama_url.rstrip('/')}/api/tags", timeout=1.5, context=ctx) as response:
                if response.status == 200:
                    ollama_reachable = True
        except:
            pass
    else:
        ollama_reachable = True

    # A configured-but-absent Ollama is only fatal when it is the only way to
    # answer a request. With cloud providers configured it is a warning: the
    # router routes around it, and doctor returning "error" for it teaches
    # agents to ignore doctor.
    ollama_expected = bool(config.get("CUSTOM_LLM_BASE_URL") and "11434" in config.get("CUSTOM_LLM_BASE_URL"))
    ollama_missing = ollama_expected and not ollama_reachable
    warnings = []
    if ollama_missing:
        msg = f"Ollama is expected at {ollama_url} but is unreachable."
        (issues if not has_providers else warnings).append(msg)

    port = args.port or 8000
    port_busy = is_port_in_use(port)
    router_here = port_busy and router_is_serving(port)
    if port_busy and not router_here:
        issues.append(f"Port {port} is already in use by something that is not RealityRouter.")

    if args.json:
        result = {
            "status": "success" if not issues else "error",
            "env_exists": env_exists,
            "sso_token_valid": sso_valid,
            "providers_configured": has_providers,
            "ollama_reachable": ollama_reachable,
            "port_available": not port_busy,
            "router_running": router_here,
            "calibration": calibration,
            "issues": issues,
            "warnings": warnings,
        }
        print(json.dumps(result, indent=2))
    else:
        print_header("RealityRouter Doctor Diagnostics")
        print(f"  Config File: {ENV_FILE} ({'Found' if env_exists else 'Missing'})")
        print(f"  SSO Token:  {'Valid/Present' if sso_valid else 'Missing'}")
        print(f"  Calibration: {calibration['state']} - {calibration['detail']}")
        print(f"  Providers:  {'Configured' if has_providers else 'None configured'}")
        print(f"  Ollama:     {'Reachable' if ollama_reachable else 'Unreachable/Not active'}")
        print(f"  Port {port}:  {'BUSY' if port_busy else 'AVAILABLE'}")
        print()
        if issues:
            print(f"  {C_RED}⚠ Diagnostics failed with the following issues:{C_RESET}")
            for iss in issues:
                print(f"    - {iss}")
        else:
            print(f"  {C_GREEN}✓ All checks passed! RealityRouter is healthy and ready.{C_RESET}")
        for w in warnings:
            print(f"  {C_YELLOW}! {w}{C_RESET}")

    if not env_exists:
        sys.exit(10)
    if not sso_valid:
        sys.exit(11)
    if not has_providers:
        sys.exit(12)
    if ollama_missing and not has_providers:
        sys.exit(13)
    if port_busy and not router_here:
        sys.exit(14)

    sys.exit(0)


def cmd_models(args, config):
    disabled = load_disabled_models()
    changed = False

    if getattr(args, "enable_all_models", False):
        disabled.clear()
        changed = True

    if getattr(args, "disable_model", None):
        disabled.add(args.disable_model)
        changed = True

    if getattr(args, "enable_model", None):
        if args.enable_model in disabled:
            disabled.remove(args.enable_model)
            changed = True

    if changed:
        save_disabled_models(disabled)
        config["DISABLED_MODELS"] = ",".join(list(disabled))
        save_env(config)

    all_models = get_all_models(config)

    if args.json:
        results = []
        for m in all_models:
            results.append({
                "id": m["id"],
                "name": m["name"],
                "provider": m["provider"],
                "enabled": m["id"] not in disabled
            })
        print(json.dumps(results, indent=2))
    else:
        print_header("RealityRouter Discovered Models")
        if not all_models:
            print("  No models discovered. Check provider credentials.")
            return

        for m in all_models:
            is_enabled = m["id"] not in disabled
            status_str = f"{C_GREEN}[✓] ENABLED {C_RESET}" if is_enabled else f"{C_RED}[✗] DISABLED{C_RESET}"
            print(f"  {status_str} {m['name']:40} ({m['provider']})")


def cmd_auth(args, config):
    if getattr(args, "auth", None) is not None:
        config["REALITY_CHECK_TOKEN"] = args.auth
        config["REALITY_CHECK_PROVIDER"] = "CLI"
        save_env(config)
        if args.json:
            print(json.dumps({"event": "auth_success", "provider": "CLI", "email": config.get("SSO_EMAIL", "anonymous")}))
        else:
            print("Authentication saved successfully via CLI argument.")
        return

    if args.agent:
        run_sso_device_flow(args, config, provider_type="m")
        return

    if args.non_interactive:
        if config.get("REALITY_CHECK_TOKEN"):
            if args.json:
                print(json.dumps({"event": "auth_success", "provider": config.get("REALITY_CHECK_PROVIDER", "Unknown"), "email": config.get("SSO_EMAIL", "anonymous")}))
            else:
                print(f"Already authenticated via {config.get('REALITY_CHECK_PROVIDER')}.")
        else:
            if args.json:
                print(json.dumps({"event": "error", "error": "No SSO token configured."}))
            else:
                print("Error: No SSO token configured.", file=sys.stderr)
            sys.exit(EXIT_NO_CREDENTIALS)
        return

    if inquirer is None:
        print("Error: Inquirer is required for interactive authentication. Use --agent or --non-interactive.", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    choices = [
        ("Login with Microsoft", "m"),
        ("Login with GitHub", "g"),
        ("Login with Google", "o"),
        ("RealitySignal Enterprise (Custom Endpoint Setup)", "e"),
    ]
    auth_q = [
        inquirer.List(
            "auth_type",
            message="Select Authentication Method",
            choices=choices,
            default="m",
        )
    ]
    auth_a = inquirer.prompt(auth_q)
    if not auth_a:
        sys.exit(0)

    auth_type = auth_a["auth_type"]
    run_sso_device_flow(args, config, auth_type)


def main():
    argv = sys.argv[1:]
    args = parse_args(argv)

    # Resolve config using strict precedence
    config = resolve_config(args)

    # Check for TTY
    check_tty(args, config)

    # Legacy Compatibility Checks
    if args.status:
        args.command = "status"
        args.json = True
    if args.set:
        for pair in args.set:
            if "=" not in pair:
                print(f"--set expects KEY=VALUE, got: {pair}", file=sys.stderr)
                sys.exit(EXIT_USAGE)
            k, v = pair.split("=", 1)
            config[k.strip().upper()] = v.strip()
        save_env(config)
        print(json.dumps({"status": "config_written", "config_file": ENV_FILE}, indent=2))
        sys.exit(EXIT_OK)

    # Default command
    if not args.command:
        args.command = "setup"

    # Route execution
    if args.command == "setup":
        cmd_setup(args, config)
    elif args.command == "start":
        cmd_start(args, config)
    elif args.command == "stop":
        cmd_stop(args, config)
    elif args.command == "status":
        cmd_status(args, config)
    elif args.command == "doctor":
        cmd_doctor(args, config)
    elif args.command == "models":
        cmd_models(args, config)
    elif args.command == "auth":
        cmd_auth(args, config)
    elif args.command == "expose":
        cmd_expose(args, config)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        sys.exit(e.code)
    except Exception as e:
        logger.error(f"Fatal crash: {e}", exc_info=True)
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
