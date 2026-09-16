# Configuration Parameter Reference (`configuration.md`)

This is the complete system parameters and environment variables reference for RealityRouter.

---

## Configuration Precedence Hierarchy

Settings are resolved during server ignition according to this strict priority chain:
```
CLI Arguments > Process Environment Variables > Config File (.env) > Auto-detect / Defaults
```

*Note: Changes made inside `~/.reality_router/.env` require a server restart to take effect.*

---

## 1. Directory & Network Binding Parameters

### `REALITY_ROUTER_HOME`
- **Purpose**: Root directory containing SQLite databases, server logs, configurations, and daemon lockfiles.
- **Type**: String (Absolute Path)
- **Default**: `~/.reality_router`
- **Security Scope**: Non-Secret
- **Subsystem**: CLI & Setup
- **Restart Required**: Yes

### `REALITY_ROUTER_PORT`
- **Purpose**: Port on which the HTTP server listens.
- **Type**: Integer (`1` - `65535`)
- **Default**: `8000` (CLI dynamically falls back if busy)
- **Security Scope**: Non-Secret
- **Subsystem**: Server Initialization
- **Restart Required**: Yes

### `REALITY_ROUTER_HOST`
- **Purpose**: Bind address for the Uvicorn network socket.
- **Type**: String (IP Address)
- **Default**: `0.0.0.0`
- **Security Scope**: Non-Secret
- **Subsystem**: Server Initialization
- **Restart Required**: Yes

---

## 2. API Credentials & Adapters

### `OPENAI_API_KEY`
- **Purpose**: Access credential for OpenAI models.
- **Type**: String (Secret)
- **Default**: None
- **Security Scope**: Secret (Redacted from logging)
- **Subsystem**: OpenAI Adapter
- **Restart Required**: Yes

### `ANTHROPIC_API_KEY`
- **Purpose**: Access credential for Anthropic models.
- **Type**: String (Secret)
- **Default**: None
- **Security Scope**: Secret (Redacted from logging)
- **Subsystem**: Anthropic Adapter
- **Restart Required**: Yes

### `GEMINI_API_KEY`
- **Purpose**: Access credential for Google Gemini.
- **Type**: String (Secret)
- **Default**: None
- **Security Scope**: Secret (Redacted from logging)
- **Subsystem**: Google Adapter
- **Restart Required**: Yes

### `MISTRAL_API_KEY`
- **Purpose**: Access credential for Mistral AI.
- **Type**: String (Secret)
- **Default**: None
- **Security Scope**: Secret (Redacted from logging)
- **Subsystem**: Mistral Adapter
- **Restart Required**: Yes

### `DEEPSEEK_API_KEY`
- **Purpose**: Access credential for DeepSeek.
- **Type**: String (Secret)
- **Default**: None
- **Security Scope**: Secret (Redacted from logging)
- **Subsystem**: DeepSeek Adapter
- **Restart Required**: Yes

### `HUGGINGFACE_API_KEY`
- **Purpose**: Access credential for Hugging Face Inference Endpoints.
- **Type**: String (Secret)
- **Default**: None
- **Security Scope**: Secret (Redacted from logging)
- **Subsystem**: Hugging Face Adapter
- **Restart Required**: Yes

### `CUSTOM_LLM_BASE_URL`
- **Purpose**: Endpoint URL for custom/local OpenAI-compatible servers.
- **Type**: String (URL)
- **Default**: None
- **Security Scope**: Non-Secret
- **Subsystem**: Generic Adapter
- **Restart Required**: Yes

### `CUSTOM_LLM_API_KEY`
- **Purpose**: Authorization key for custom OpenAI-compatible server.
- **Type**: String (Secret)
- **Default**: `dummy`
- **Security Scope**: Secret
- **Subsystem**: Generic Adapter
- **Restart Required**: Yes

---

## 3. Reality Signal Calibration

### `REALITY_CHECK_TOKEN`
- **Purpose**: SSO Device Authorization token verifying your workstation.
- **Type**: String (Bearer Token)
- **Default**: None
- **Security Scope**: Secret (Redacted from logging)
- **Subsystem**: Calibration / Remote Feedback
- **Restart Required**: Yes

### `REALITY_CHECK_PROVIDER`
- **Purpose**: Authenticated SSO Identity provider.
- **Type**: String (`Microsoft` / `GitHub` / `Google` / `CLI`)
- **Default**: None
- **Security Scope**: Non-Secret
- **Subsystem**: Calibration
- **Restart Required**: Yes

### `REALITY_ROUTING_URL`
- **Purpose**: API endpoint querying single-shot expected-utility predictions.
- **Type**: String (URL)
- **Default**: `https://api.realitysignal.com/v1/snap`
- **Security Scope**: Non-Secret
- **Subsystem**: Routing Core
- **Restart Required**: Yes

### `REALITY_REROUTING_URL`
- **Purpose**: API endpoint querying sequential assessment calibration curves.
- **Type**: String (URL)
- **Default**: `https://api.realitysignal.com/v1/ladder`
- **Security Scope**: Non-Secret
- **Subsystem**: Routing Core
- **Restart Required**: Yes

---

## 4. Routing Formulas & Weights

### `ROUTING_STRATEGY`
- **Purpose**: Strategy selection for choosing candidate models.
- **Type**: String (`expected_utility` or `tiered_assessment`)
- **Default**: `expected_utility`
- **Security Scope**: Non-Secret
- **Subsystem**: Routing Core
- **Restart Required**: Yes

### `COST_SENSITIVITY` (alpha)
- **Purpose**: Penalty weight per dollar in Expected Utility scoring. Larger values strongly avoid expensive models.
- **Type**: Float (`0.0` to `100.0`)
- **Default**: `0.5`
- **Security Scope**: Non-Secret
- **Subsystem**: Utility Calculation
- **Restart Required**: Yes

### `TIME_SENSITIVITY` (beta)
- **Purpose**: Penalty weight per second of response time. Larger values strongly favor fast models.
- **Type**: Float (`0.0` to `100.0`)
- **Default**: `0.5`
- **Security Scope**: Non-Secret
- **Subsystem**: Utility Calculation
- **Restart Required**: Yes

### `SENTIMENT_MODEL_ID`
- **Purpose**: Designated cheap/fast model ID performing user satisfaction checks in the feedback loop.
- **Type**: String (Model ID)
- **Default**: Discovered cheap model (e.g. `gpt-4o-mini`)
- **Security Scope**: Non-Secret
- **Subsystem**: Sentiment Feedback Loop
- **Restart Required**: Yes

---

## 5. Security Options

### `INSECURE_SKIP_TLS_VERIFY`
- **Purpose**: Allows connecting to custom enterprise endpoints with self-signed SSL/TLS certificates.
- **Type**: Boolean (`true` / `false`)
- **Default**: `false`
- **Security Scope**: Non-Secret
- **Subsystem**: HTTP Clients / Discovery Paths
- **Restart Required**: Yes
- **⚠️ WARNING**: Setting to `true` bypasses SSL chain verification. Use strictly in secured sandbox private networks.
