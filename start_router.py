#!/usr/bin/env python3
import json
import logging
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import inquirer

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
    with open(ENV_FILE, "w") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")


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


def sync_discover_openai_compat(base_url, api_key, provider_name):
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
                    c_url, env_vars.get("CUSTOM_LLM_API_KEY", "dummy"), "custom"
                )
            )
    # OpenAI
    oa_key = env_vars.get("OPENAI_API_KEY")
    if oa_key and oa_key != "dummy":
        models.extend(
            sync_discover_openai_compat("https://api.openai.com/v1", oa_key, "openai")
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

        # 2. Try OpenAI compat endpoint if needed (fallback if native found nothing)
        if not models:
            for version in ["v1beta", "v1"]:
                compat_models = sync_discover_openai_compat(
                    f"https://generativelanguage.googleapis.com/{version}/openai",
                    g_key,
                    "gemini",
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
                "https://api.anthropic.com/v1", a_key, "anthropic"
            )
        )

    # Mistral
    mi_key = env_vars.get("MISTRAL_API_KEY")
    if mi_key and mi_key != "dummy":
        models.extend(
            sync_discover_openai_compat("https://api.mistral.ai/v1", mi_key, "mistral")
        )

    # DeepSeek
    ds_key = env_vars.get("DEEPSEEK_API_KEY")
    if ds_key and ds_key != "dummy":
        models.extend(
            sync_discover_openai_compat(
                "https://api.deepseek.com/v1", ds_key, "deepseek"
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
                        "https://api.openai.com/v1", new_val, "openai"
                    )
                elif choice == "gemini":
                    # Simple check for gemini discovery
                    test_models = sync_discover_openai_compat(
                        "https://generativelanguage.googleapis.com/v1beta/openai",
                        new_val,
                        "gemini",
                    )
                elif choice == "anthropic":
                    test_models = sync_discover_openai_compat(
                        "https://api.anthropic.com/v1", new_val, "anthropic"
                    )
                elif choice == "mistral":
                    test_models = sync_discover_openai_compat(
                        "https://api.mistral.ai/v1", new_val, "mistral"
                    )
                elif choice == "deepseek":
                    test_models = sync_discover_openai_compat(
                        "https://api.deepseek.com/v1", new_val, "deepseek"
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
                                test_url, test_key, "custom"
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
                {"client_id": client_id, "scope": "openid User.Read"}
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


def start_server(env_vars):
    print_header("Final Step: Ignition")
    print_status("Building environment and launching core...")

    env = os.environ.copy()
    env.update(env_vars)
    # Ensure PYTHONPATH includes the absolute path to the core source
    env["PYTHONPATH"] = os.path.abspath(REALITY_ROUTER_DIR)

    port = find_available_port(8000)
    if port != 8000:
        print_status(f"Port 8000 in use, automatically switched to {port}", "warn")

    print(f"\n  {C_GREEN}{C_BOLD}Server active at http://0.0.0.0:{port}{C_RESET}")
    sentiment_model = env_vars.get("SENTIMENT_MODEL_ID", "Not Configured")
    print(f"  {C_YELLOW}Sentiment Model: {sentiment_model}{C_RESET}")
    print(f"  {C_CYAN}Press [CTRL+C] to stop the process.{C_RESET}\n")
    print(f"{C_BLUE}" + "━" * 64 + f"{C_RESET}")

    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "src.main:app",
                "--host",
                "0.0.0.0",
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
      - "{port}:8000"
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

        subprocess.run(cmd, cwd=SCRIPT_DIR, check=True)

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


def main():
    logger.debug("Starting Reality Router Setup Wizard.")
    env_vars = load_env()
    has_docker = check_docker()

    # Check if we should skip to start
    if os.path.exists(ENV_FILE):
        print_header("Reality Router")
        print_status("Welcome back! Existing config detected.\n")
        if not env_vars.get("SENTIMENT_MODEL_ID"):
            print_status(
                "A Sentiment Model has not been configured yet. Reconfiguration required.",
                "warn",
            )
            action = "r"
            time.sleep(2)
        else:
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
            start_server(env_vars)
            return
        elif action == "d":
            deploy_docker(env_vars)
            return

    try:
        print_header("Reality Router Setup")
        print(
            f"  Welcome to the {C_BOLD}Reality Router{C_RESET} initialization wizard."
        )
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
            current_token = env_vars.get("REALITY_CHECK_TOKEN")
            current_provider = env_vars.get("REALITY_CHECK_PROVIDER")

            if not current_token:
                provider = wizard_reality_check_auth(env_vars)
                if provider:
                    current_provider = provider
                else:
                    current_provider = "None (Skipped)"

            clear_screen()
            print_header("Authentication Status")
            if env_vars.get("REALITY_CHECK_TOKEN"):
                print(
                    f"  {C_GREEN}{C_BOLD}{ICON_CHECK} Authenticated securely via {current_provider} SSO.{C_RESET}\n"
                )
                confirm_choices = [
                    ("Continue with the setup", "c"),
                    ("Go back and change authentication", "b"),
                ]
                default_choice = "c"
            else:
                print(
                    f"  {C_RED}⚠ Authentication failed. A valid SSO token is required.{C_RESET}\n"
                )
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
                if "REALITY_CHECK_TOKEN" in env_vars:
                    del env_vars["REALITY_CHECK_TOKEN"]
                if "REALITY_CHECK_PROVIDER" in env_vars:
                    del env_vars["REALITY_CHECK_PROVIDER"]

        # Run remaining steps in order
        wizard_user_profile(env_vars)
        wizard_routing_strategy(env_vars)
        wizard_global_settings(env_vars)
        wizard_providers(env_vars)
        wizard_model_management(env_vars)

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
                deploy_docker(env_vars)
                return

        start_server(env_vars)

    except (KeyboardInterrupt, EOFError):
        print(f"\n\n  {C_RED}Setup aborted.{C_RESET}")
    except Exception as e:
        logger.error(f"Fatal crash: {e}")


if __name__ == "__main__":
    main()
