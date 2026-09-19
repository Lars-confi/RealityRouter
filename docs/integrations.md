# Developer & Client Tool Integrations (`integrations.md`)

RealityRouter serves three client APIs: OpenAI Chat Completions (`/v1/chat/completions`), the OpenAI Responses API (`/v1/responses`) and Anthropic Messages (`/v1/messages`). Any developer tool, IDE or CLI agent that lets you set a custom endpoint for one of them can route through it, usually in a minute of configuration.

---

## Client compatibility

Every row marked ✅ was verified end to end against a running router: the client
sent a real request, completed a tool call where it supports them, and appeared
by name in the dashboard's **Agent Activity** panel. The versions are the ones
tested — newer releases usually work, but these are the ones there is evidence
for. Verified September 2026.

| Client | Verified version | Protocol | Tool calls | Setup | Shows in dashboard as | Guide |
| :--- | :---: | :--- | :---: | :--- | :--- | :--- |
| **OpenCode** | `1.18.15` | Chat Completions | ✅ | config file | `opencode/…` | [Guide](#opencode) |
| **VS Code** (built-in chat) | `1.138` | Chat Completions | ✅ | JSON config | `GitHubCopilotChat/…` | [Guide](#vs-code-built-in-chat) |
| **Cline** | `4.1.19` | Chat Completions | ✅ | settings panel | `Cline/…` | [Guide](#cline-vs-code) |
| **Zed** | `1.20.2` | Chat Completions | ✅ | `settings.json` | `Zed/…` | [Guide](#zed) |
| **Aider** | `0.86.2` | Chat Completions | ✅ | CLI or config file | `OpenAI/Python…` unless configured — see guide | [Guide](#aider) |
| **Codex CLI** | `0.154.0` | Responses | ✅ | `config.toml` | `codex_exec/…` | [Guide](#codex-cli) |
| **Claude Code** | `2.1.278` | Anthropic Messages | ✅ | environment variables | `claude-cli/…` | [Guide](#claude-code-dedicated-guide) |
| **OpenClaw** | `2026.4.14` | Chat Completions or Responses | ✅ | config file | `OpenClaw` | [Guide](#openclaw) |
| **Hermes** | `0.10.0` | Chat Completions | ✅ | `config.yaml` | `Hermes` | [Guide](#hermes) |
| **Cursor** | `3.x`, Pro plan | Chat Completions, sent from Cursor's cloud | ✅ | settings panel + public URL + API key | `Cursor/1.0` | [Guide](#cursor) — **cannot reach `localhost`; needs a public URL** |
| **Roo Code** | not verified | OpenAI-compatible | — | settings panel | — | [Guide](#roo-code) |

---

## 💡 Dynamic Port & Endpoint Discovery

While the human examples below use port `8000` for readability, **agents and programmatic scripts must not assume the port is 8000.** 
To discover the correct running endpoint, execute:
```bash
reality-router status --json
```
And parse the `"base_url"` key. For GUI configuration, always check `reality-router status` first to find the current active address.

---

## 🔑 API keys (required before exposing the router)

By default the router accepts any request, which is fine while it listens only
on `localhost` or a private network. **Before making it reachable from anywhere
else** — a public tunnel, a server, a shared network — turn on API keys.

1. Generate a long random key:
   ```bash
   openssl rand -hex 32
   ```
2. Add it to `~/.reality_router/.env` (several keys can be comma-separated, for
   example one per person or tool, so each can be revoked alone):
   ```
   ROUTER_API_KEYS=<your-key>
   ```
3. Restart the router (`reality-router stop && reality-router start`, or restart
   the container). The log confirms: *Inbound API-key auth is on (1 key(s)
   configured).*

From then on, every client must send one of the keys, in whichever form it
already uses:

| Sent as | Used by |
| :--- | :--- |
| `Authorization: Bearer <key>` | OpenAI-compatible clients: Cursor, OpenCode, Cline, Zed, Codex, Aider, VS Code, OpenClaw |
| `x-api-key: <key>` | Anthropic clients: Claude Code |
| Browser login prompt (any username, key as password) | The dashboard |

In practice: put the key wherever this page says `rr-local`. Requests without a
valid key get `401`. `/health` stays open so monitoring keeps working.

The key only authenticates clients to the router. It is never sent on to a
provider; the router always calls providers with its own keys.

---

## Claude Code (Dedicated Guide)

Claude Code is Anthropic's agentic CLI tool. It speaks the Anthropic Messages
API, and RealityRouter serves that API directly at `/v1/messages`, translating to
whichever provider it picks. There is no adapter to set up: point Claude Code at
the router with two environment variables.

### Integration Details
1. **Protocol**: Anthropic Messages (`/v1/messages`), including tool calls and streaming.
2. **Routing**: each request is routed across your whole pool, not only Anthropic models.
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
4. **Tool Calls**: tool calling and streaming work, and each step is routed on its own, so routine steps can go to cheaper models and harder ones to stronger models.
   > Claude Code's prompts are tuned for Claude models. Small non-Claude models
   > sometimes choose wrong file paths in its tool calls (for example `/mnt/data/`).
   > If you see that, keep Claude models in your pool for this agent.
5. **Billing**: with `ANTHROPIC_BASE_URL` set, requests go to the router and are paid for through the provider keys configured in RealityRouter, not a Claude subscription.
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

## VS Code (built-in chat)

VS Code's own chat and agent mode can use RealityRouter as a custom model. **No
GitHub account and no Copilot plan are needed.** VS Code makes the requests
itself, so a router on your own machine works directly.

Three things in the setup are easy to trip over:

- The chat's model picker offers only *Sign in to use Copilot*. That is not the
  way in — use the Command Palette.
- The **Add Models** flow never asks for a URL. You fill it in afterwards, in JSON.
- Models are unavailable in **Restricted Mode**. Trust the workspace first.

1. If VS Code opened the folder in Restricted Mode, trust the workspace.
2. Command Palette → **Chat: Manage Language Models** → **Add Models** → **Custom Endpoint**.
3. Name the group, enter `rr-local` as the API key, and choose **Chat Completions** as the API type.
4. Command Palette → **Chat: Open Language Models (JSON)**, and fill in the model:

```json
[
  {
    "name": "Reality Router",
    "vendor": "customendpoint",
    "apiKey": "${input:chat.lm.secret.…}",
    "apiType": "chat-completions",
    "models": [
      {
        "id": "auto",
        "name": "Reality Router (auto)",
        "url": "http://localhost:8000/v1",
        "toolCalling": true,
        "vision": true,
        "maxInputTokens": 128000,
        "maxOutputTokens": 16000
      }
    ]
  }
]
```

Leave the `apiKey` line exactly as VS Code wrote it — it points at the stored
secret rather than holding the key. The `url` is the `/v1` base; VS Code appends
`/chat/completions` itself.

5. Choose **Reality Router (auto)** in the chat's model picker.

Inline code completions stay on GitHub's models. This covers chat, agent mode
and utility tasks.

**Verify:** ask anything, then look for `GitHubCopilotChat/…` in the dashboard's
Agent Activity.

---

## Cursor

> [!WARNING]
> **Current Cursor cannot use a router running on your own machine.** Cursor
> sends chat requests from its own cloud, not from your computer, and its cloud
> refuses private addresses — `localhost`, `127.0.0.1`, `192.168.x.x`, Tailscale
> addresses. The error is *"Provider returned error: Access to private networks
> is forbidden."* This affects every local proxy — Ollama, LM Studio, LiteLLM —
> not only RealityRouter.

What was measured with Cursor 3.x on a Pro plan, against a router at a public
HTTPS address:

- **Chat and agent mode route correctly**, including full tool-call round trips:
  Cursor sent its tool definitions, the routed model called one, Cursor executed
  it and returned the result.
- Requests arrive from Cursor's servers, identified as `Cursor/1.0`.
- The value in **OpenAI API Key** is forwarded unchanged, as
  `Authorization: Bearer <key>`, so key authentication on the router works.
- **Override OpenAI Base URL** requires a Pro plan. A free account is asked to
  upgrade before anything is sent.
- Tab autocomplete never uses a custom endpoint.

So Cursor needs the router at a public HTTPS address, protected by an API key:

1. **Turn on API keys first** — see [API keys](#-api-keys-required-before-exposing-the-router).
   Do not skip this: without a key, a public router is an open proxy, and anyone
   who finds the address can spend your provider credits.
2. **Give the router a public HTTPS address.** The quickest is a Cloudflare
   quick tunnel, which needs no account:
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
   It prints an address like `https://<random-words>.trycloudflare.com`. That
   address changes every time the tunnel restarts; for a permanent one, use a
   named Cloudflare tunnel or ngrok with a reserved domain.
3. In **Cursor Settings → Models**:
   - **OpenAI API Key:** your router key (not an OpenAI key).
   - **Override OpenAI Base URL:** on, set to `https://<your-address>/v1`.
   - **Add model:** `auto`.
4. Pick `auto` in the chat's model picker and send a message.

**Verify:** the dashboard's Agent Activity shows `Cursor/1.0`. Stop the tunnel
when you are not using it.

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

Cline makes requests from your own machine, so a local router works directly.

On first run Cline asks how you want to use it — choose **Bring my own API key**.
Later, the same settings are under the gear icon in the Cline panel.

1. **API Provider:** **OpenAI Compatible**
2. **Base URL:** `http://localhost:8000/v1`
3. **OpenAI Compatible API Key:** `rr-local`
4. **Model ID:** `auto`
5. Click **Continue**.

Cline asks before it writes files (**Save / Reject**). That is Cline's normal
behaviour, not the router.

**Verify:** give Cline a task, then look for `Cline/…` in the dashboard's Agent
Activity.

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

> [!IMPORTANT]
> Current Codex CLI speaks **only** the OpenAI Responses API. It calls
> `/v1/responses`, and `wire_api = "chat"` is rejected outright
> (*`wire_api = "chat"` is no longer supported*). Leave `wire_api` unset.
> This needs a router build that serves `/v1/responses`; on one that does not,
> Codex fails with a 404 on that path.

**Verify:** run `codex exec "say hello"`, then look for `codex_exec/…` in the
dashboard's Agent Activity.

---

## Zed

Zed makes requests from your own machine, so a local router works directly. No
Zed account is needed.

Add an OpenAI-compatible provider to `~/.config/zed/settings.json`:

```json
{
  "language_models": {
    "openai_compatible": {
      "reality-router": {
        "api_url": "http://localhost:8000/v1",
        "available_models": [
          {
            "name": "auto",
            "display_name": "Reality Router (auto)",
            "max_tokens": 128000,
            "capabilities": {
              "tools": true,
              "images": false,
              "parallel_tool_calls": false,
              "prompt_cache_key": false
            }
          }
        ]
      }
    }
  },
  "agent": {
    "default_model": { "provider": "reality-router", "model": "auto" }
  }
}
```

The API key is entered in the provider's settings in Zed, or supplied as an
environment variable named after the provider id — for `reality-router` that is
`REALITY_ROUTER_API_KEY`. Any placeholder such as `rr-local` works. Zed's docs
ask that keys stay out of `settings.json`.

Using `openai_compatible`, rather than overriding the built-in `openai`
provider, keeps your real OpenAI access working alongside the router.

**Verify:** open the Agent panel, check **Reality Router (auto)** is selected,
ask anything, then look for `Zed/…` in the dashboard's Agent Activity.

---

## OpenClaw

OpenClaw makes requests from the machine it runs on, so a local router works
directly. Add RealityRouter as a custom provider in `~/.openclaw/openclaw.json`:

```json
{
  "agents": {
    "defaults": {
      "model": { "primary": "rr/auto" },
      "models": { "rr/auto": { "alias": "RR" } }
    }
  },
  "models": {
    "mode": "merge",
    "providers": {
      "rr": {
        "baseUrl": "http://localhost:8000/v1",
        "apiKey": "rr-local",
        "api": "openai-responses",
        "models": [
          {
            "id": "auto",
            "name": "Reality Router (auto)",
            "reasoning": false,
            "input": ["text"],
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": 128000,
            "maxTokens": 8192
          }
        ]
      }
    }
  }
}
```

Both `"api": "openai-responses"` (OpenClaw's recommendation for custom endpoints)
and `"api": "openai-completions"` work. `"mode": "merge"` keeps your other
providers available. Leave `cost` at zero: the router does its own cost
accounting, and OpenClaw cannot know which model a request will land on.

If OpenClaw runs as a service, restart its gateway after editing the file.

**Verify:** `openclaw agent --local -m "Create hello.txt containing hi"`, then
look for `OpenClaw` in the dashboard's Agent Activity.

---

## Hermes

Hermes runs on your own machine, so a local router works directly. Point its
model at the router in `~/.hermes/config.yaml`:

```yaml
model:
  provider: custom
  base_url: http://localhost:8000/v1
  api_key: rr-local
  api_mode: chat_completions
  default: auto
  context_length: 128000
```

`provider: custom` is what selects an OpenAI-compatible endpoint; the router
appears under that name rather than as a named provider. Restart the gateway
after editing, if you run one.

> [!NOTE]
> **If Hermes has many MCP servers connected, keep an eye on the tool count.**
> OpenAI models reject requests carrying more than 128 tool definitions, and a
> Hermes instance with a dozen MCPs can exceed that on its own. The router will
> fall through to a model that accepts them, but if your pool is mostly OpenAI
> models, trim the toolsets Hermes sends.

**Verify:** `hermes chat -q "what is in the current directory?"`, then look for
`Hermes` in the dashboard's Agent Activity.

---

## Roo Code

Roo Code settings are UI-driven:
1. Open the Roo Code settings panel.
2. Choose **OpenAI Compatible** as the provider.
3. Configure the Base URL to your RealityRouter address.
4. Input `auto` as the custom model name.
