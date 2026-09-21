---
title: Agent-assisted install
description: Let a coding agent install and wire up RealityRouter for you
---

# Agent-assisted install

You can ask a coding agent to set RealityRouter up rather than doing it by hand.
Two skills cover it, and they are deliberately separate — you install the router
once, but you connect tools repeatedly.

| Skill | Run |
|---|---|
| `install-realityrouter` | Once |
| `connect-tool-to-realityrouter` | Once per tool |

## Setup

In Claude Code, install the plugin:

```
/plugin marketplace add Lars-confi/RealityRouter
/plugin install reality-router@confidentia
```

That is the whole setup. Both skills arrive namespaced —
`reality-router:install-realityrouter` and
`reality-router:connect-tool-to-realityrouter` — and update when you pull.

<details>
<summary>Copying the files instead</summary>

```bash
git clone https://github.com/Lars-confi/RealityRouter
mkdir -p ~/.claude/skills
cp -r RealityRouter/skills/* ~/.claude/skills/
```
</details>

For other runtimes, see [`skills/README.md`](https://github.com/Lars-confi/RealityRouter/blob/main/skills/README.md).
The skills are plain Markdown and the content is portable; only the file
location differs.

Then ask:

> Install RealityRouter and point OpenCode at it.

## What happens

The agent checks prerequisites, installs into `~/.reality_router`, asks you for
provider keys, starts the router, and **routes a real request through it** before
showing you anything. That last step matters: an empty dashboard proves nothing.

It then configures your tool and confirms the tool appears by name in the
dashboard's Agent Activity panel. That tag is the proof — a written config file
is not.

## What the agent will not do

**It will not obtain an API key on its own.** It cannot generate one, read one
out of your shell history or another project's config, or reuse one it saw
earlier in the session for something else. Every key comes from you, and it goes
to `~/.reality_router/.env` and nowhere else.

Telling the agent to "just set it up" does not change this.

**It cannot configure every tool.** Cursor, Cline and Roo Code are configured
through settings panels with no underlying config file. For those the agent
gives you the exact click path and waits for you to confirm you saved, rather
than claiming success.

Fully automatable: OpenCode, Codex CLI, Aider, Zed, Hermes, Continue/VSCodium.

## Doing it without an agent

Nothing here is agent-only. The same flags work from a shell, a Dockerfile or a
systemd unit:

```bash
reality-router setup --non-interactive --set DEEPSEEK_API_KEY=...
reality-router start --non-interactive --detach     # returns once /health is healthy
reality-router status --json                        # JSON: port, base_url, dashboard_url
```

### Legacy Compatibility Note (Deprecated Syntax)
For backward compatibility, the following older syntax is still supported by the CLI but is deprecated in favor of the command-oriented subcommands above:
```bash
reality-router --headless --set DEEPSEEK_API_KEY=...
reality-router --headless --detach
reality-router --headless --status
```

Read the port from that JSON rather than assuming 8000 — RealityRouter moves to
the next free port when 8000 is taken.

See the [Quickstart](quickstart.md) for the interactive wizard, and
[Tool integrations](integrations.md) for per-tool configuration.
