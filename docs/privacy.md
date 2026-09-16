# Privacy & Data-Flow Reference (`privacy.md`)

Trust and privacy are core principles of RealityRouter. RealityRouter is designed to run **directly on your machine or private servers** as a local routing layer. 

This document explains what data is stored locally, what data is sent to your configured model providers, and what data is communicated to the Reality Signal calibration servers.

---

## Direct vs. Proxied Connections

1. **No Hosted Model Proxy**: Raw prompt data and model completions **never** pass through RealityRouter-operated hosted cloud proxies. Your local RealityRouter server communicates **directly** with the API endpoints of your selected model providers (e.g., OpenAI, Anthropic, Gemini, DeepSeek, Ollama).
2. **Metadata-Only Calibration**: RealityRouter only communicates with the remote Reality Signal calibration service to calculate routing coefficients (Snap) or sequential assessments (Ladder). This communication uses **anonymous, high-level task features (like token counts and language presence) rather than raw prompt text**.

---

## Explicit Data-Flow Matrix

| Data Element | Stored Locally? | Sent to Model Provider? | Sent to Reality Signal? | Purpose & Implementation Details |
| :--- | :---: | :---: | :---: | :--- |
| **Raw User Prompt** | RAM / Optional Log | **Yes** (Selected) | **NO** | Sent directly to the selected model provider to execute inference. |
| **System Prompt** | RAM / Optional Log | **Yes** (Selected) | **NO** | Sent directly to the selected model provider. |
| **Tool Schema / Tool Calls** | RAM / Optional Log | **Yes** (Selected) | **NO** | Sent directly to the selected model provider to enable function calling. |
| **Extracted Task Features** | SQLite / Log | **NO** | **Yes** (Snap/Ladder) | Token counts, presence of code, presence of json, and general classification tokens are sent to compute optimal model scores. |
| **Agent Fingerprint** | SQLite | **NO** | **Yes** | Sent as an anonymous identifier to track performance history for personalized curves. |
| **Selected Model ID** | SQLite / Log | **Yes** (Selected) | **Yes** | Shared to calibrate model-specific performance and cost over time. |
| **Validation Outcome** | SQLite / Log | **NO** | **Yes** | Shared as feedback to tune accuracy expectations (conformal/Venn calibration). |
| **Sentiment Result** | SQLite / Log | **NO** | **Yes** | Shared as feedback (quality vs dissatisfaction metrics) to tune model quality scores. |
| **User Email** | `.env` | **NO** | **Yes** | Used to authenticate your license/session with Reality Signal. |
| **User Location** | `.env` | **NO** | **Yes** | Sent to optimize regional latency modeling. |
| **Provider API Keys** | `.env` (Secure file) | **Yes** (Selected) | **NO** | Used to authorize request calls to OpenAI/Anthropic etc. |
| **Reality Signal Token** | `.env` (Secure file) | **NO** | **Yes** | Authorizes your local router with Reality Signal Snap and Ladder endpoints. |
| **Cost & Latency Metrics** | SQLite | **NO** | **Yes** | Spent tokens and latency are reported to update global and local pricing tables. |

---

## Local Storage Footprint

All local states are persisted within the configuration home directory (default: `~/.reality_router/`):
1. **`router.db` (SQLite)**: Stores local execution history, latency metrics, and success feedback.
2. **`.env`**: Stores encrypted/plain text API keys, tokens, and configuration.
3. **`disabled_models.json`**: List of models you have manually quarantined/disabled.
4. **`server.log`**: Standard operational logs. API keys and passwords are systematically redacted prior to writing.

---

## Disabling Reality Signal

If you choose to run in a completely air-gapped environment without any external network traffic other than model providers:
1. You can disable the Reality Signal token (`REALITY_CHECK_TOKEN`).
2. **Functional Impact**:
   - The router will fall back to **static expected-utility heuristics** based on local historical pricing tables and fixed latency defaults.
   - Dynamic conformal calibration and sequential Ladder assessment are disabled.
   - Dynamic regional latency discovery is disabled.
