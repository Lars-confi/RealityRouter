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
| **LM Studio / vLLM**| `CUSTOM_LLM_BASE_URL` | Yes | Yes | Yes | No | No | Yes | OpenAI-Compatible |

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

## 2. Local & OpenAI-Compatible Providers

For local models or models from specialized clouds, use the generic **OpenAI-Compatible** adapter.

### Ollama (Local Offline Runtimes)
- **Base URL**: `CUSTOM_LLM_BASE_URL=http://localhost:11434` (or `http://localhost:11434/v1`)
- **Key**: `CUSTOM_LLM_API_KEY=dummy`
- **Features**: Automatically queries tags (`/api/tags`) to discover models and maps local inference weights. Local models are priced at **zero marginal cost** in utility calculations.

### Generic Enterprise & Alternative Endpoints (Kimi, GLM, Grok, Qwen, etc.)
- Set `CUSTOM_LLM_BASE_URL` to point to the provider's OpenAI-compatible endpoint.
- Set `CUSTOM_LLM_API_KEY` to your secret key.
- Discovered models will inherit baseline pricing models or default to generic tiered-pricing metadata during Snap decision scoring.
