# Agent skills

Two skills that let a coding agent install RealityRouter and wire a tool to it,
instead of the user doing it by hand.

| Skill | What it does |
|---|---|
| [`install-realityrouter`](install-realityrouter/SKILL.md) | Installs, configures and verifies the router. Run once. |
| [`connect-tool-to-realityrouter`](connect-tool-to-realityrouter/SKILL.md) | Points one coding tool at a running router. Run once per tool. |

They are separate on purpose: you install the router once, but you wire up
tools repeatedly, often months apart. Adding Cursor to an existing setup should
not run install logic.

## What the agent will and will not do

**Will:** check prerequisites, install, discover models, start the server, route
a real request to prove the chain works, write the tool's config file, open the
dashboard.

**Will not:** obtain an API key on its own. It cannot generate one, read one out
of your shell history or another project's config, or reuse one it saw earlier
for something else. Every provider key comes from you, and the agent writes it
to `~/.reality_router/.env` and nowhere else.

Some tools cannot be automated at all. Cursor, Cline and Roo Code are configured
through settings panels with no config file, so the agent gives you the exact
click path and waits — it will not claim success until you confirm you saved.

## Installing the skills

### Claude Code

```bash
mkdir -p ~/.claude/skills
cp -r skills/install-realityrouter ~/.claude/skills/
cp -r skills/connect-tool-to-realityrouter ~/.claude/skills/
```

Project-scoped instead of personal? Use `.claude/skills/` in the repo.

### Everything else

The skills are plain Markdown with YAML frontmatter, and the content is
runtime-agnostic — only the file location and frontmatter convention differ.
For a runtime that reads a rules or instructions file, point it at the canonical
copies:

```
https://raw.githubusercontent.com/Lars-confi/RealityRouter/main/skills/install-realityrouter/SKILL.md
https://raw.githubusercontent.com/Lars-confi/RealityRouter/main/skills/connect-tool-to-realityrouter/SKILL.md
```

Fetching rather than copying means the instructions stay current as tool
configuration changes — which it does, frequently.

`https://realityrouter.dev/llms.txt` indexes both for agents that look there
first.

## Requirements

`install-realityrouter` uses the non-interactive flags on `start_router.py`
(`--headless`, `--set`, `--detach`, `--status`). Without them the skill falls
back to handing off to the interactive wizard, which works but is not
unattended.
