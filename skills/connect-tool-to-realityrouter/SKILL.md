---
name: connect-tool-to-realityrouter
description: Point a coding tool at an already-running RealityRouter so its requests get routed across models. Covers OpenCode, Cursor, Aider, Cline, Codex CLI, Zed, Hermes, Continue/VSCodium, OpenClaw and Roo Code. Use when the user asks to connect, point, route or wire a tool through RealityRouter, to add another tool to an existing router, to make their editor or agent use the router, or asks why their tool is still billing a provider directly. Safe to run repeatedly, once per tool. If RealityRouter is not installed yet, use install-realityrouter first.
---

# Connect a tool to RealityRouter

RealityRouter presents an OpenAI-compatible endpoint. Connecting a tool means
changing three settings in that tool:

- **Base URL** — the router's `base_url`, established in Step 0
- **API key** — `rr-local` (a placeholder; RealityRouter does not check it)
- **Model** — `auto` (the router chooses per request)

**Do not hardcode port 8000.** RealityRouter moves to the next free port when
8000 is taken, so a tool configured against an assumed port will silently keep
using its own provider. Always use the `base_url` from Step 0.

This skill assumes the router is already running. It is **idempotent and
re-runnable** — run it again for each tool the user wants to add.

Nothing here touches provider credentials. The only secret-shaped value you
write is `rr-local`, which is not a secret. The one exception is Hermes, whose
config file holds a real provider key you will be replacing — see below.

---

## Step 0 — Find the router

If `install-realityrouter` handed you a `base_url`, use it and skip ahead.
Otherwise ask the router where it is:

```bash
reality-router --headless --status
```

This prints JSON with `status`, `port`, `url` (the origin), `base_url` and
`dashboard_url`. Keep all three URLs — the later steps use each of them.

- `"status": "healthy"` — continue to Step 1.
- `"status": "stopped"` — installed but not running. Start it with
  `reality-router --headless --detach`, then use the `base_url` it returns.
- Command not found — not installed by that route. Try `curl -s
  http://localhost:8000/health` in case a container is serving it; if that
  fails too, **invoke `install-realityrouter`** and come back.

Do not configure a tool to point at something that is not answering.

## Step 1 — Which tool?

**Do not assume the tool is the one you are running inside.** The user may be
asking you from one tool and wanting a different one routed.

Detect what is present:

```bash
ls ~/.config/opencode/opencode.json 2>/dev/null   # OpenCode
ls ~/.codex/config.toml 2>/dev/null               # Codex CLI
ls ~/.aider.conf.yml 2>/dev/null                  # Aider
ls ~/.config/zed/settings.json 2>/dev/null        # Zed
ls ~/.hermes/config.yaml 2>/dev/null              # Hermes
ls ~/.continue/config.json 2>/dev/null            # Continue / VSCodium
ls ~/.cursor 2>/dev/null                          # Cursor
command -v openclaw 2>/dev/null                   # OpenClaw
```

List what you found and **ask which one to configure**. If several are present,
do not choose for them. If they name a tool you did not detect, continue anyway
— it may be installed somewhere non-standard.

## Step 2 — Get the current instructions

Fetch the integration doc rather than reciting settings from memory. These
change, and the doc is kept current:

```
https://raw.githubusercontent.com/Lars-confi/RealityRouter/main/docs/integrations.md
```

It covers OpenCode, Cursor, Aider, Cline and Codex CLI in detail, including the
per-tool caveats. Follow it over anything you remember.

## Step 3 — Configure

Tools split into two groups, and this determines what you can actually do.

### 3a. Config-file tools — you write these

| Tool | File |
|---|---|
| OpenCode | `~/.config/opencode/opencode.json` |
| Codex CLI | `~/.codex/config.toml` |
| Aider | `~/.aider.conf.yml`, or `OPENAI_API_BASE` + `OPENAI_API_KEY` env |
| Zed | `~/.config/zed/settings.json` |
| Hermes | `~/.hermes/config.yaml` → the `model:` block |
| Continue / VSCodium | `~/.continue/config.json` |
| OpenClaw | consult its own docs — do not guess the path |

Show the user the diff you intend to make, get agreement, back up the file,
then write it yourself.

**Hermes** keeps its settings in the `model:` block. Change `base_url`,
`default` and `api_key`; leave `provider` and `api_mode` as they are, and
preserve the existing `context_length`:

```yaml
model:
  provider: custom
  api_mode: chat_completions
  base_url: <base_url>          # from Step 0, e.g. http://localhost:8000/v1
  default: auto
  api_key: rr-local
```

The `api_key` you are replacing is a **real provider key**. Back the file up
and do not print the old value.

### 3b. UI-driven tools — you guide, the user clicks

**Cursor, Cline and Roo Code have no config file to edit.** You cannot do this
for the user.

Give them the exact click path and the three values, then **wait for them to
confirm they have saved**. Do not report success until they say so.

- **Cursor** — Settings → Models → Add Model. Enable *Override OpenAI Base
  URL*, set the base URL and key above, add model ids (`auto` plus any specific
  ones they want), click Verify. Disable overlapping pre-enabled models.
- **Cline** — ⚙️ in the Cline panel → API Provider → *OpenAI Compatible*. Set
  base URL, key and model `auto`. Leave "use different models for Plan and Act"
  **off** and set both to `auto`; the router already picks stronger models for
  planning-shaped calls and cheaper ones for execution.

## Step 4 — Verify from inside the tool

A written config is not proof. Have the user send one real prompt from the tool
they just configured, then:

```bash
curl -s --max-time 5 "<url>/metrics/summary" | head -c 400
```

Then open the dashboard and look at the **Agent Activity** section:

```
<dashboard_url>
```

RealityRouter identifies the calling client automatically — it recognises
OpenClaw, Hermes, Aider and Roo Code from the request itself, and Zed, Cursor
and Continue from their User-Agent. The tool should appear there by name with
its own request count and spend. **That tag is the proof the tool is routed**,
not the fact that the config file was written.

If the request count did not move, the tool is still going direct. Go back to
Step 3; for UI tools the most common cause is the user not having hit Save.

---

## If something fails

- **Tool errors on connect** — check the base URL includes `/v1`, that the
  port matches what `--status` reported, and that `/health` still answers. An
  assumed port 8000 when the router moved elsewhere is the most likely cause.
- **"Model not found"** — the tool is sending a model id the router does not
  know. Set it to `auto`.
- **Requests still hit the provider directly** — the tool kept its own base
  URL. Some tools have per-project config that overrides the global file.
- **Tab completion still uses the vendor model** (Cursor) — expected. Override
  applies to Chat, Composer, Cmd+K and Agent; autocomplete stays on Cursor's
  own model and is not intercepted.

Never work around a failure by pointing the tool at a provider directly. If it
cannot be routed, say so and leave the original config intact.

## Adding another tool later

Run this skill again. It changes nothing about the router itself, so
configuring a second or third tool is safe and needs no reinstall.
