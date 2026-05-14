---
title: Quickstart
description: 60-second install + your first routed request
---

# Quickstart

Install the router, configure your providers, and route your first request — in about 60 seconds.

> [!NOTE]
> Requires Python 3.10+ and an API key from at least one LLM provider (OpenAI, Anthropic, Gemini, Mistral, HuggingFace, or a local Ollama instance).

## 1. Install

Download the dist zip and run the startup script:

```bash
unzip reality_router_dist.zip
cd reality_router_dist
./start.sh
```

The script checks for Python 3, creates a virtualenv at `venv/`, installs dependencies, and migrates any existing config to `~/.reality_router/`. Then it launches the interactive setup wizard.

## 2. Run the wizard

The wizard walks you through five steps:

### Step 1 — Routing strategy

Pick one:

- **Expected Utility (single-shot)** — score every model upfront, route to the winner. Lowest latency, best for balanced workloads. *Default.*
- **Tiered Assessment (sequential)** — start with the cheapest model, validate output, escalate only if needed. Maximum cost optimization, best for autonomous agents.

Both strategies use the same Expected Utility math. See [Routing strategies](./routing.md) for details.

### Step 2 — Sensitivities

These weight the Expected Utility equation. Defaults work for most workloads.

- `R` — value of a correct answer. Higher → router prefers more accurate (often more expensive) models.
- `α` — cost penalty per dollar spent. Higher → router prefers cheaper models, including local ones.
- `β` — latency penalty per second. Higher → router prefers faster models. If unset, defaults to `1 − α`.

### Step 3 — Provider credentials

Paste API keys for the providers you want available:

- `OPENAI_API_KEY` — OpenAI
- `ANTHROPIC_API_KEY` — Anthropic
- `GEMINI_API_KEY` — Google Gemini
- `MISTRAL_API_KEY` — Mistral
- `HUGGINGFACE_API_KEY` — Hugging Face
- `CUSTOM_LLM_BASE_URL` + `CUSTOM_LLM_API_KEY` — local Ollama, vLLM, or any OpenAI-compatible endpoint

Keys are saved to `~/.reality_router/.env`. They never leave your machine.

### Step 4 — Discover & enable models

The router probes each provider and lists every available model. Toggle them ON/OFF based on what you want to route to. Set per-model concurrency limits to avoid overwhelming providers (especially local Ollama). Pick one cheap fast model (Haiku, Gemini Flash) as your **sentiment analyzer** — it watches user follow-up turns for "unhappy" signals and feeds them back into the router's probability estimates.

### Step 5 — Ignition

The wizard launches the server on `http://localhost:8000`. The web dashboard is at `http://localhost:8000/metrics/dashboard`.

## 3. Your first routed request

Point any OpenAI-compatible client at the router:

```python
import openai

openai.api_base = "http://localhost:8000/v1"
openai.api_key  = "anything"   # router uses its own .env keys upstream

response = openai.ChatCompletion.create(
    model="auto",   # let the router decide per-query
    messages=[
        {"role": "user", "content": "Add a formatDate(date) helper to utils.ts"}
    ],
)

print(response.choices[0].message.content)
```

Or via cURL — same OpenAI-compatible shape:

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [
      {"role": "user", "content": "Add a formatDate helper"}
    ]
  }'
```

## 4. Watch it route

Open a second terminal and launch the CLI event viewer to see every routing decision in real time, including the per-model leaderboard:

```bash
source venv/bin/activate
python reality-router/event_viewer.py
```

Press `[h]` for the System Health Report with 🟢🟡🔴 reliability per model. Press `[1-5]` to inspect any recent event's full payload and utility breakdown. Press `[q]` to quit.

## Next

- [How it works](./concepts.md) — the math behind every routing decision.
- [Multi-agent support](./agents.md) — sticky sessions for Cursor, Zed, Claude Code, OpenClaw.
- [Dashboard](./dashboard.md) — CLI + web monitoring for spend and reliability.
