---
title: Quickstart
description: 60-second install + your first routed request
---

# Quickstart

Install RealityRouter, configure your providers, and route your first request — in about 60 seconds.

> [!NOTE]
> Requires Python 3.10+ (recommended) or Docker. You will need an API key from at least one LLM provider (OpenAI, Anthropic, Gemini, Mistral, DeepSeek, Moonshot, Z.ai, xAI, Alibaba Qwen, or a local Ollama instance).

## 1. Install

The fastest way to get started is using the one-line installer.

### Linux / macOS
```bash
curl -fsSL https://raw.githubusercontent.com/Lars-confi/RealityRouter/main/install.sh | bash
```

### Windows (PowerShell)
```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; iex ((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/Lars-confi/RealityRouter/main/install.ps1'))
```

The script will download the latest version, set up the environment, and launch the interactive setup wizard.

## 2. Run the wizard

The wizard walks you through the configuration of your decision engine:

### Step 1 — Authentication
RealityRouter uses **Reality Signal™** to estimate model success probabilities. You will be prompted to authenticate via GitHub, Google, or Microsoft SSO to receive your secure calibration token.

### Step 2 — Routing Strategy
Pick your default behavior:
- **Expected Utility (Snap)** — Single-shot routing to the best model. Lowest latency.
- **Tiered Assessment (Ladder)** — Sequential escalation. Starts cheap, moves to smarter models only if validation fails.

### Step 3 — Intelligence Coefficients
Fine-tune how the engine weights different factors:
- **α (Cost Sensitivity)** — Preference for saving money.
- **β (Time Sensitivity)** — Preference for speed/low latency.

### Step 4 — Provider Credentials
Enter your API keys for providers like OpenAI, Anthropic, Mistral, DeepSeek, Moonshot (Kimi), Z.ai (GLM), xAI (Grok), and Alibaba Qwen, or provide the URL for a local Ollama instance. The wizard **live-validates** your keys to ensure they are working before you finish.

### Step 5 — Model Visibility
The router auto-discovers all available models based on your keys. Toggle models **ON** or **OFF** to define your routing pool. You will also select a cheap, fast model (like Gemini Flash) to act as your **Sentiment Analyzer** for the feedback loop.

## 3. Your first routed request

RealityRouter exposes an OpenAI-compatible API. The supported endpoints and features are listed in the API reference. Just point your client to your local instance:

### Python (OpenAI 1.x Client)
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="rr-local",  # The router handles upstream auth
)

response = client.chat.completions.create(
    model="auto",  # Let the router choose the best model
    messages=[
        {"role": "user", "content": "Write a high-performance Rust function to parse JSON."}
    ],
)

print(response.choices[0].message.content)
```

### curl (Protocol Smoke Test)
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer any" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello RealityRouter!"}]
  }'
```

## 4. Control Center

Once running, visit the web dashboard to monitor your routing in real-time:

- **Dashboard:** `http://localhost:8000/metrics/dashboard`

Here you can adjust your **Cost vs. Speed** preferences using live sliders and see exactly how much you are saving compared to using flagship models for every request.

---

## 5. For AI Agents (Automated Quickstart)

If you are an AI coding agent or automation script, you can complete the installation, configuration, diagnostics, and launch without interactive setup prompts. User action may still be required to provide API credentials or complete authentication:

### One-Click Installation
```bash
# Idempotent Linux install
curl -fsSL https://raw.githubusercontent.com/Lars-confi/RealityRouter/main/install.sh | bash
```

### Auto-Configure and Zero-Prompt Setup
Scan environment variables, configure defaults, and resolve local Ollama models instantly:
```bash
reality-router setup --agent
```

### Automated Diagnostic Doctor
Verify that configurations, SSO, and ports are correctly prepared:
```bash
reality-router doctor --json
```

### Start Router in Background
Start RealityRouter in detached daemon mode:
```bash
reality-router start --agent --detach --port 8000
```

---

## Next

- [How it works](./concepts.md) — The math behind Expected Utility.
- [Routing strategies](./routing.md) — Snap vs. Ladder mode.
- [Multi-agent support](./agents.md) — Using RealityRouter with Cursor, Zed, and Roo Code.