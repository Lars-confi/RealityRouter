# Developer & Client Tool Integrations (`integrations.md`)

RealityRouter speaks the standard OpenAI API protocol. Any developer tool, IDE, or CLI agent that allows you to configure a custom OpenAI-compatible endpoint can seamlessly route through it — usually in under a minute of configuration.

---

## Authoritative Client Integration Matrix

| Client | Tested Version | Protocol | Tools | Streaming | Auto-Configurable | Guide |
| :--- | :---: | :--- | :---: | :---: | :---: | :--- |
| **OpenCode** | `1.4.2` | OpenAI-compatible | Yes | Yes | Yes (Config-file) | [Guide](#opencode) |
| **Cursor** | `0.45.0`| OpenAI-compatible | Yes | Yes | No (GUI Settings) | [Guide](#cursor) |
| **Aider** | `0.65.1`| OpenAI-compatible | Yes | Yes | Yes (CLI/Config) | [Guide](#aider) |
| **Claude Code**| `0.2.1`| OpenAI-compatible | Yes | Yes | Yes (Env/CLI) | [Guide](#claude-code-dedicated-guide) |
| **Cline** | `2.5.0` | OpenAI-compatible | Yes | Yes | No (GUI Settings) | [Guide](#cline-vs-code) |
| **Codex CLI** | `0.9.1` | OpenAI-compatible | Yes | Yes | Yes (Config-file) | [Guide](#codex-cli) |
| **Zed** | `0.151.1`| OpenAI-compatible | Yes | Yes | Yes (settings.json)| [Guide](#zed) |
| **Roo Code** | `1.12.0`| OpenAI-compatible | Yes | Yes | No (GUI Settings) | [Guide](#roo-code) |
| **OpenClaw** | `0.10.0`| OpenAI-compatible | Yes | Yes | Yes (Config-file) | [Guide](#openclaw) |

---

## 💡 Dynamic Port & Endpoint Discovery

While the human examples below use port `8000` for readability, **agents and programmatic scripts must not assume the port is 8000.** 
To discover the correct running endpoint, execute:
```bash
reality-router status --json
```
And parse the `"base_url"` key. For GUI configuration, always check `reality-router status` first to find the current active address.

---

## Claude Code (Dedicated Guide)

Claude Code is Anthropic's agentic CLI tool. Since Claude Code expects Anthropic-native message schemas (`/v1/messages`), connecting it to RealityRouter requires setting up an OpenAI-compatible adapter translation.

### Integration Details
1. **Protocol**: Claude Code communicates with Anthropic endpoints by default. To route requests through RealityRouter, you must configure it to point to our OpenAI-compatible gateway.
2. **Workaround / Translation**:
   - Set your system environment variable `ANTHROPIC_BASE_URL` to point to RealityRouter's base address.
   - Claude Code handles the Anthropic keys locally, but RealityRouter intercepts and routes based on your central pool.
3. **Environment Configuration**:
   ```bash
   # Read actual port from reality-router status first!
   # No /v1 on the end: Claude Code appends /v1/messages itself.
   export ANTHROPIC_BASE_URL="http://localhost:8000"
   export ANTHROPIC_API_KEY="rr-local" # Placeholder
   ```
   > **Do not add `/v1`.** With `http://localhost:8000/v1` Claude Code calls
   > `/v1/v1/messages`, gets a 404, and reports it misleadingly as
   > *"There's an issue with the selected model."*
4. **Tool Calls & Reasoning**: Tool calling and streaming work natively. When Claude Code executes command-line operations or code edits, the task features (number of tools, length) are extracted by RealityRouter to determine if a cheaper model (like Gemini Flash) or a flagship model (like Claude Sonnet) should run the step.
5. **Subscription / OAuth**: Claude Code's OAuth/Google SSO keys can coexist seamlessly; RealityRouter does not interfere with client-side credential persistence.
6. **Dashboard Verification**: Check `http://localhost:8000/metrics/dashboard` under "Per-Agent Activity" to see calls tagged with the agent fingerprint `claude-cli/<version>` (for example `claude-cli/2.1.278`).

---

## OpenCode

OpenCode is configured via configuration files.

Edit `~/.config/opencode/opencode.json` (or a per-project `opencode.json`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "reality-router": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Reality Router",
      "options": {
        "baseURL": "http://localhost:8000/v1",
        "apiKey": "rr-local"
      },
      "models": {
        "auto":              { "name": "RR Auto" }
      }
    }
  }
}
```

*Note: Available models depend on your configured providers. Run `reality-router models` to view your active pool.*

---

## Cursor

To configure Cursor to route through RealityRouter:
1. Open **Cursor Settings → Models**.
2. Click **Add Model** and enter `auto`.
3. Enable **Override OpenAI Base URL**.
4. Set **Base URL** to `http://localhost:8000/v1` (or your current active Base URL).
5. Set **API Key** to `rr-local`.
6. Disable the default Cursor models to prevent conflicting routes.

---

## Aider

Aider supports direct custom endpoint configuration using command-line arguments, environment variables, or a YAML configuration file.

### CLI Setup
```bash
aider \
  --openai-api-base http://localhost:8000/v1 \
  --openai-api-key rr-local \
  --model openai/auto
```

### YML Configuration (`.aider.conf.yml`)
```yaml
openai-api-base: http://localhost:8000/v1
openai-api-key: rr-local
model: openai/auto
```

> [!IMPORTANT]
> Aider model IDs must be prefixed with `openai/` (e.g., `openai/auto`) so that its underlying LiteLLM engine routes the request to your custom OpenAI-compatible endpoint rather than standard OpenAI servers.

### Naming Aider in the dashboard

Aider reaches the router through LiteLLM's OpenAI SDK and sends nothing that
identifies it — its User-Agent is `OpenAI/Python`, and its system prompt never
mentions Aider. Left alone it appears in Agent Activity as `OpenAI/Python …`,
indistinguishable from any other Python client.

To have it show up as Aider, tell it to send the `X-Agent-ID` header. Add a
`.aider.model.settings.yml` in your project or home directory:

```yaml
- name: openai/auto
  extra_params:
    extra_headers:
      X-Agent-ID: "aider"
```

The name is yours to choose — use something like `aider-backend` if you want to
separate one checkout's usage from another's.

**Verify:** ask Aider anything, then check the dashboard's Agent Activity panel.
Without the header you will see `OpenAI/Python …`; with it, the name you set.

---

## Cline (VS Code)

1. Click the **settings (gear) icon** in the Cline panel.
2. Select **OpenAI Compatible** from the API Provider list.
3. Set **Base URL** to your active RealityRouter URL (e.g. `http://localhost:8000/v1`).
4. Set **API Key** to `rr-local`.
5. Set **Model** to `auto`.

---

## Codex CLI

Codex CLI stores its configuration in `~/.codex/config.toml`:

```toml
model = "auto"
model_provider = "reality-router"

[model_providers.reality-router]
name = "Reality Router"
base_url = "http://localhost:8000/v1"
env_key = "OPENAI_API_KEY"
```

Export your placeholder key:
```bash
export OPENAI_API_KEY="rr-local"
```

*Note: Codex CLI is configured to use the standard OpenAI completion and chat wire formats. The custom `/responses` endpoint is not implemented, and the default OpenAI wire is used.*

---

## Zed

Zed supports custom OpenAI-compatible language model configuration in its `settings.json`:

```json
{
  "language_models": {
    "openai": {
      "api_url": "http://localhost:8000/v1",
      "available_models": [
        {
          "name": "auto",
          "max_tokens": 128000
        }
      ]
    }
  }
}
```

---

## Roo Code

Roo Code settings are UI-driven:
1. Open the Roo Code settings panel.
2. Choose **OpenAI Compatible** as the provider.
3. Configure the Base URL to your RealityRouter address.
4. Input `auto` as the custom model name.
