---
name: install-realityrouter
description: Install and verify RealityRouter — a self-hosted, OpenAI-compatible LLM router that scores every model on expected utility and sends each request to the cheapest one likely to succeed. Use when the user asks to install RealityRouter, set up an LLM router or gateway, cut their model spend, or route between multiple providers. This skill installs the router itself; use connect-tool-to-realityrouter afterwards to point a coding tool at it.
---

# Install RealityRouter

RealityRouter is a self-hosted, OpenAI-compatible gateway. For each request it
scores every model in the user's pool on expected utility — probability of
success, cost, latency — and routes to the best one. The user brings their own
provider keys; nothing is proxied through a third party.

This skill gets a working, verified router. It does not configure any client —
that is `connect-tool-to-realityrouter`, which you should invoke at the end.

---

## Rules you must not break

**Never obtain an API key on your own.** Do not generate, guess, or invent one.
Do not read one out of the user's shell history, environment variables, other
config files, credential stores, or any other project. Do not reuse a key you
happen to have seen earlier in this session for a different purpose. Every
provider key must come from the user, in this conversation, in response to you
asking for it.

**Never send a key anywhere except the local config file.** Keys go into
`~/.reality_router/.env` on the user's own machine and nowhere else. Do not put
one in a shell command that gets logged, do not echo it back into the
conversation, do not commit it, and do not transmit it to any endpoint other
than the provider's own API during verification.

**You do the writing, not the user.** Once the user gives you a key, you write
the config yourself. Do not ask them to open a file, hand them lines to paste,
or tell them to edit anything by hand. Supplying or confirming a key is the
only manual step this skill should create.

**Say what you are doing; do not ask permission twice.** If the user asked you
to install RealityRouter, install it. State the source and destination in one
line and proceed — a confirmation prompt for something they just requested is
friction, not safety. Name each config file as you write it, listing variable
names but never values. Announce, do not ask.

The exception is arriving here without an install request — the user asked
something looser, like how to cut their model costs, and installing software is
your inference rather than their instruction. Say what you propose and get a
yes before touching their machine.

**"Just set it up" waives none of the above.** Installing software and handling
provider credentials is high-trust. If the user declines to supply a key for a
provider, continue without it; RealityRouter works with one.

---

## Step 0 — Is it already there?

**Do not assume port 8000.** RealityRouter moves to the next free port when
8000 is taken, so the port must be read, never guessed. Ask the router itself:

```bash
reality-router --headless --status
```

That prints JSON including `port`, `base_url` and `dashboard_url`. Use those
values for every later step in this skill — never a hardcoded URL.

- `"status": "healthy"` — installed and running. **Do not reinstall.** Skip to
  Step 7 and hand off to `connect-tool-to-realityrouter`, passing the
  `base_url`.
- `"status": "stopped"` — installed but not running. Skip to Step 4.

If the `reality-router` command does not exist, it is not installed by this
route. Check whether something is already serving it anyway — a container, for
instance:

```bash
curl -s --max-time 3 http://localhost:8000/health
```

A healthy response there means an instance you did not install is running. Say
so and ask the user whether to use it or install a separate one; do not install
over the top of it.

Otherwise, continue.

## Step 1 — Prerequisites

```bash
command -v git python3 curl
python3 --version    # needs 3.10+
```

Report anything missing and stop. Do not install system packages or package
managers as a side effect of this skill.

## Step 2 — Install

Say in one line that you are cloning `github.com/Lars-confi/RealityRouter` into
`~/.reality_router` and building a virtualenv there, then run it:

```bash
curl -fsSL https://raw.githubusercontent.com/Lars-confi/RealityRouter/main/install.sh | bash
```

Non-interactive; it prompts for nothing.

## Step 3 — Ask for provider keys

Tell the user which providers are supported and ask which they have keys for:

> OpenAI, Anthropic, Google Gemini, Mistral, DeepSeek, Moonshot (Kimi),
> Z.ai (GLM), xAI (Grok), Alibaba Qwen, or a local Ollama instance.

A router needs **two or more** models to have anything to choose between.
Encourage at least two — ideally one cheap and one strong, because that price
gap is where the saving comes from. One is enough to start.

Ask **one provider at a time**, naming the variable before you ask:

| Provider | Variable |
|---|---|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Gemini | `GEMINI_API_KEY` |
| Mistral | `MISTRAL_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| Moonshot (Kimi) | `MOONSHOT_API_KEY` |
| Z.ai (GLM) | `ZAI_API_KEY` |
| xAI (Grok) | `XAI_API_KEY` |
| Alibaba Qwen | `DASHSCOPE_API_KEY` |
| Ollama (local) | `CUSTOM_LLM_BASE_URL` — no key needed |

Write each one with `--set` rather than editing the file by hand — it owns the
`.env` format and knows which keys are legal:

```bash
reality-router --headless --set DEEPSEEK_API_KEY=... --set OPENAI_API_KEY=...
```

Never echo a key value back into the conversation once you have it.

## Step 4 — Start it

```bash
reality-router --headless --detach
```

This discovers models, picks a sentiment model for the feedback loop, starts
the server, waits until `/health` is healthy, and prints status JSON. **Capture
that JSON** — `base_url`, `dashboard_url` and `port` all come from it, and are
what the remaining steps use.

Exit codes tell you what to do next, so check them rather than parsing prose:

| Code | Meaning | What to do |
|---|---|---|
| 0 | Running | Continue |
| 3 | No credentials configured | Go back to Step 3 |
| 4 | Credentials present, no models discovered | A key is wrong — the `providers` block names which |
| 5 | Started but never became healthy | Report the `log` path from the output |
| 6 | Port busy | Only happens with an explicit `--port` |
| 7 | Already running | Use `--status`; do not start a second one |

**3 and 4 need opposite responses.** 3 means ask the user for a key. 4 means a
key they already gave was rejected — say which provider, do not re-ask for all
of them.

The output also reports `sentiment_model` and `sentiment_model_reason`. That is
a choice the user would otherwise have made in the wizard, so tell them which
model was picked and why.

## Step 5 — Confirm every provider came up

Using the `base_url` from Step 4:

```bash
curl -s --max-time 5 "<base_url>/models"
```

It should list models from **every** provider whose key was supplied. If one is
missing, its key is wrong or its endpoint was unreachable — the `providers`
block in the status JSON gives a per-provider `reason`. Name the provider, and
do not silently continue.

## Step 6 — Route one real request

Do this **before** showing the dashboard. An empty dashboard proves nothing and
looks broken.

```bash
curl -s --max-time 60 "<base_url>/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Reply with the single word: routed"}]}'
```

A 200 with a completion means the chain works end to end. **Tell the user which
model the router actually picked** — that is the interesting part, and it is
what they are about to see in the dashboard.

## Step 7 — Open the dashboard, then hand off

Use the `dashboard_url` from Step 4:

```bash
open "<dashboard_url>"       # macOS
xdg-open "<dashboard_url>"   # Linux
```

The Step 6 request is already in it. Point out cost, the always-flagship
counterfactual, and per-model reliability, and mention that the α (cost) and
β (latency) sliders change routing live with no restart.

Then say the router is running but nothing is using it yet, and offer to wire
up their coding tool — **invoke `connect-tool-to-realityrouter`, passing the
`base_url`** so it does not have to rediscover the port.

---

## If something fails

- **Install fails** — report the error verbatim. Do not retry with `sudo`.
- **A provider is missing from `/v1/models`** — that key is wrong or the
  endpoint is unreachable. Name the provider; do not re-ask more than once.
- **Step 6 returns non-200** — the router is up but cannot reach any provider.
  Show the response body. Do not fall back to calling a provider directly.

Never work around a failure by sending the user's traffic anywhere other than
their own RealityRouter instance.

---

## Status

Steps 3, 4 and 5 depend on `--headless`, `--set`, `--detach` and `--status`,
which are in **PR #4** and not yet merged. Until that lands, this skill cannot
run unattended: fall back to telling the user to run `reality-router`, walk the
wizard, and say when it is up — then continue from Step 5, reading the port
from the wizard's own output rather than assuming 8000.

One remaining gap, not blocking: settings load at import from `.env`, and
although `reload_settings()` exists in `src/config/settings.py` no endpoint
exposes it, so a key added after startup needs a restart. Tracked in issue #3.

Deliberately **not** proposed: an HTTP endpoint for setting keys. The API is
currently unauthenticated — `POST /metrics/preferences` returns 200 with no
credentials — so a credential-writing endpoint would be reachable by any local
process and by any web page via CSRF. Headless flags keep credentials on the
process-start path and add no network surface.
