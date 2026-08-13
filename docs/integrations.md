---
title: Tool integrations
description: Connect Reality Router to OpenCode, Cursor, Aider, Cline, and Codex CLI
---

# Tool integrations

Reality Router speaks the OpenAI protocol. Any developer tool that lets you configure a custom OpenAI-compatible endpoint can route through it — usually in under a minute of config.

This page covers the five most-requested integrations. The pattern is the same for anything else: point the tool's OpenAI base URL at `http://localhost:8000/v1` (or your RR host), give it a placeholder API key, and pick a model from the RR pool.

> [!NOTE]
> All snippets assume RR is running locally on port 8000. Swap `localhost` for your host if RR runs on a different machine (e.g. a home lab, VPC, or Tailscale endpoint).

## OpenCode

Config-file only — [OpenCode](https://opencode.ai) doesn't have a GUI for custom OpenAI-compatible providers.

Edit `~/.config/opencode/opencode.json` (or a per-project `opencode.json`):

```jsonc
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
        "auto":              { "name": "RR Auto" },
        "claude-opus-5":     { "name": "Opus 5 via RR" },
        "gpt-5":             { "name": "GPT-5 via RR" },
        "claude-haiku-4-5":  { "name": "Haiku 4.5 via RR" }
      }
    }
  }
}
```

**Verify:** run `/models` in the OpenCode TUI, select `reality-router / auto`, then ask any trivial prompt. The call appears in the RR dashboard's Agent Activity table tagged `opencode/…`.

## Cursor

UI-driven. Open **Cursor Settings → Models**, click **Add Model**, then:

- Enable **Override OpenAI Base URL**
- **Base URL:** `http://localhost:8000/v1`
- **API Key:** `rr-local`
- Add model IDs: `auto`, `claude-opus-5`, `gpt-5`, `claude-haiku-4-5`
- Click **Verify**

Disable any of Cursor's pre-enabled models that overlap.

> [!NOTE]
> Override OpenAI Base URL is a mode switch. Chat, Composer, Cmd+K, and Agent all route through RR. Tab autocomplete stays on Cursor's proprietary model (included in Cursor's plan; RR doesn't intercept it).

**Verify:** ask Cursor's Chat any prompt, then check the RR dashboard for a request tagged `Cursor/…`.

## Aider

Three interchangeable configuration paths — pick whichever fits your workflow.

### Command-line flags

```bash
aider \
  --openai-api-base http://localhost:8000/v1 \
  --openai-api-key rr-local \
  --model openai/auto
```

### Environment variables

```bash
export OPENAI_API_BASE=http://localhost:8000/v1
export OPENAI_API_KEY=rr-local
aider --model openai/auto
```

### `.aider.conf.yml` (project or `~/`)

```yaml
openai-api-base: http://localhost:8000/v1
openai-api-key: rr-local
model: openai/auto
```

> [!IMPORTANT]
> Model IDs must be prefixed with `openai/` (e.g. `openai/auto`). Without the prefix, LiteLLM (Aider's provider layer) tries to route to actual OpenAI rather than your custom endpoint.

**Verify:** start Aider, ask anything, check the dashboard for a request tagged `aider-chat/…`.

## Cline (VS Code)

Native support for custom OpenAI-compatible providers.

- Click the **⚙️ settings icon** in the Cline panel
- **API Provider** dropdown → **OpenAI Compatible**
- **Base URL:** `http://localhost:8000/v1`
- **API Key:** `rr-local`
- **Model:** `auto` (no prefix required)
- Under **Advanced**, enable **Image Support** (if you route to vision-capable models) and **Computer Use** (for tool calls)
- Click **Verify**

> [!TIP]
> Cline's Plan/Act mode lets you configure separate models for each phase. Leave "Use different models for Plan and Act" **off** and set both modes to `auto` — RR routes per call, so Plan-mode calls naturally get stronger models and Act-mode calls get cheaper ones without any manual split.

**Verify:** ask any prompt in the Cline panel, check the dashboard for a request tagged `Cline/…`.

## Codex CLI

TOML config in `~/.codex/config.toml`:

```toml
model = "auto"
model_provider = "reality-router"

[model_providers.reality-router]
name = "Reality Router"
base_url = "http://localhost:8000/v1"
env_key = "OPENAI_API_KEY"
```

Then export a placeholder key:

```bash
export OPENAI_API_KEY="rr-local"
```

> [!NOTE]
> Reserved provider IDs (`openai`, `ollama`, `lmstudio`) can't be reused for custom providers. `reality-router` is fine. `wire_api` defaults to OpenAI-compatible — set `wire_api = "responses"` only if pointing at RR's `/responses` endpoint.

**Verify:** run `codex`, ask any prompt, check the dashboard for a request tagged `Codex-CLI/…`.

## Composability with other gateways

Reality Router does not replace API gateways like [Portkey](https://portkey.ai) or [Kong AI Gateway](https://konghq.com). It sits behind them as the routing brain:

```
Your app → Portkey / Kong → Reality Router → OpenAI / Anthropic / DeepSeek
```

Both Portkey and Kong let you configure custom OpenAI-compatible upstreams. Point them at your RR instance to get their gateway features (observability, org policy, rate limiting) plus RR's per-call model selection.

## Your tool isn't listed?

Any client that accepts a custom OpenAI base URL will work. Follow the OpenCode or Codex CLI pattern for config-file tools; the Cursor or Cline pattern for UI-driven tools. Open an issue on [GitHub](https://github.com/Lars-confi/RealityRouter) if you'd like a specific tool added to this page.
