# Model Provider Capability & Pricing Matrix (`providers.md`)

RealityRouter integrates with a wide variety of model providers, ranging from public cloud foundation models to enterprise private API endpoints and local CPU/GPU offline runtimes.

---

## Authoritative Provider Matrix

| Provider | Credential Variable | Auto-Discovery | Chat | Streaming | Tools | Vision | Tested | Support Class |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **OpenAI** | `OPENAI_API_KEY` | Yes | Yes | Yes | Yes | Yes | Yes | First-Class |
| **Anthropic** | `ANTHROPIC_API_KEY` | Yes | Yes | Yes | Yes | Yes | Yes | First-Class |
| **Google Gemini** | `GEMINI_API_KEY` | Yes | Yes | Yes | Yes | Yes | Yes | First-Class |
| **Mistral AI** | `MISTRAL_API_KEY` | Yes | Yes | Yes | Yes | No | Yes | First-Class |
| **DeepSeek** | `DEEPSEEK_API_KEY` | Yes | Yes | Yes | Yes | No | Yes | First-Class |
| **Hugging Face** | `HUGGINGFACE_API_KEY` | Yes | Yes | Yes | No | No | Yes | First-Class |
| **Ollama** | `CUSTOM_LLM_BASE_URL` | Yes | Yes | Yes | Yes | No | Yes | First-Class (Local) |
| **Moonshot / Kimi** | `CUSTOM_LLM_BASE_URL` / Key | Yes | Yes | Yes | Yes | No | Yes | OpenAI-Compatible |
| **Z.ai / GLM** | `CUSTOM_LLM_BASE_URL` / Key | Yes | Yes | Yes | Yes | No | Yes | OpenAI-Compatible |
| **xAI / Grok** | `CUSTOM_LLM_BASE_URL` / Key | Yes | Yes | Yes | Yes | No | Yes | OpenAI-Compatible |
| **Alibaba Qwen** | `CUSTOM_LLM_BASE_URL` / Key | Yes | Yes | Yes | Yes | No | Yes | OpenAI-Compatible |
| **OpenRouter** | `OPENROUTER_API_KEY` | Yes | Yes | Yes | Yes | Yes | Yes | Aggregator |
| **LM Studio** | `LM_STUDIO_BASE_URL` | Yes | Yes | Yes | Yes | No | Yes | First-Class (Local) |
| **vLLM / other local** | `CUSTOM_LLM_BASE_URL` | Yes | Yes | Yes | No | No | Yes | OpenAI-Compatible |

---

## 1. First-Class Providers

First-class providers are natively integrated into RealityRouter's configuration wizard and core adapters. Discovered models automatically retrieve correct pricing metadata and regional latency parameters.

### OpenAI
- **Key**: `OPENAI_API_KEY`
- **Default Endpoint**: `https://api.openai.com/v1`
- **Supported Features**: Full support for tool calling, JSON outputs, streaming, and image inputs.

### Anthropic
- **Key**: `ANTHROPIC_API_KEY`
- **Default Endpoint**: `https://api.anthropic.com/v1`
- **Supported Features**: Full support for native tool calling, streaming, vision (Claude 3.5 Sonnet / Opus).

### Google Gemini
- **Key**: `GEMINI_API_KEY`
- **Native Endpoint**: `https://generativelanguage.googleapis.com/v1beta`
- **Supported Features**: Supports native Google API formats as well as OpenAI-compatible translations for tool calling and streaming.

---

## 1b. OpenRouter (Aggregator)

- **Key**: `OPENROUTER_API_KEY`
- **Endpoint**: `https://openrouter.ai/api/v1` (routed natively by LiteLLM as `openrouter/...`)
- **Pricing**: taken live from OpenRouter's own `/models` response, so costs are
  exact and stay current rather than coming from a curated table.

OpenRouter resells roughly 450 models from every major vendor behind a single
key. Registering all of them would bury the models you deliberately chose, and
would price the same model twice whenever you also hold a direct key. So the
default is a **gap-filler**, not a firehose:

| Rule | Why |
| :--- | :--- |
| Vendors whose own key is set are skipped | `openai/*` is dropped when `OPENAI_API_KEY` is set, so you do not get the same model at two prices |
| Anything priced at $0 is skipped **by default** | Covers `:free` variants *and* unlabelled stealth models. Zero cost wins every routing decision outright, and these are rate limited hard enough that winning is the problem. See the override below — this is a default, not a ban |
| Models that cannot call tools are skipped | An agent pool of chat-only models is not useful |
| At most 2 models per vendor | One prolific vendor (Qwen alone lists 50+) would otherwise flood the pool |

In a typical install with OpenAI and Anthropic keys present, this registers
around 70 models instead of 450.

**Overrides:**

```bash
OPENROUTER_MODELS=llama,nemotron    # keep only ids matching these substrings
OPENROUTER_MODELS=all               # keep everything except $0 and non-text models
OPENROUTER_MAX_PER_VENDOR=5         # widen the per-vendor cap (default 2)
OPENROUTER_ALLOW_FREE=true          # allow $0 models everywhere
```

`OPENROUTER_MODELS` takes precedence over every gap-filler rule, including the
per-vendor cap and the duplicate-vendor skip.

### Using the free models

The $0 exclusion is a default, not a prohibition. There are two ways past it:

```bash
# Name it. Asking for a model by name is an explicit choice, so $0 is allowed:
OPENROUTER_MODELS=ling-3.0-flash-sante
#   -> registers openrouter/inclusionai/ling-3.0-flash-sante:free

# Or lift the rule entirely, for the default and "all" modes:
OPENROUTER_ALLOW_FREE=true
```

Worth knowing what you are opting into. A model costing $0 maximises
`EU = P(success) x Reward - a*Cost - b*Latency` on the cost term, so it wins
every routing decision until its measured `P(success)` drops far enough to
outweigh that. Since the free tiers are aggressively rate limited, the failures
that teach the router are also the requests your users are waiting on. Fine for
a workstation, worth thinking about in production.

---

## 2. Local & OpenAI-Compatible Providers

### LM Studio (Local)

- **Key**: `LM_STUDIO_BASE_URL`, default `http://localhost:1234/v1`
- **Optional**: `LM_STUDIO_API_KEY` (LM Studio ignores it; defaults to `lm-studio`)

LM Studio serves the OpenAI protocol from its Developer tab. Point the router at
it and every loaded model is registered as `lmstudio/{model}` — namespaced so
that loading the same GGUF in both LM Studio and Ollama does not collide in the
model table.

Local models are registered at **zero cost**, the same treatment Ollama gets:
locally served tokens cost nothing per request, so the routing decision turns on
`P(success)` and latency alone.

Start LM Studio, load a model, and enable the local server under Developer →
Start Server. If nothing is listening the router logs it and moves on; an absent
LM Studio is not an error.


For local models or models from specialized clouds, use the generic **OpenAI-Compatible** adapter.

### Ollama (Local Offline Runtimes)
- **Base URL**: `CUSTOM_LLM_BASE_URL=http://localhost:11434` (or `http://localhost:11434/v1`)
- **Key**: `CUSTOM_LLM_API_KEY=dummy`
- **Features**: Automatically queries tags (`/api/tags`) to discover models and maps local inference weights. Local models are priced at **zero marginal cost** in utility calculations.

### Generic Enterprise & Alternative Endpoints (Kimi, GLM, Grok, Qwen, etc.)
- Set `CUSTOM_LLM_BASE_URL` to point to the provider's OpenAI-compatible endpoint.
- Set `CUSTOM_LLM_API_KEY` to your secret key.
- Discovered models will inherit baseline pricing models or default to generic tiered-pricing metadata during Snap decision scoring.
