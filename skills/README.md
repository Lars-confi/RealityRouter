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

```
/plugin marketplace add Lars-confi/RealityRouter
/plugin install reality-router@confidentia
```

Both skills arrive namespaced, as `reality-router:install-realityrouter` and
`reality-router:connect-tool-to-realityrouter`, and update when the repository
does.

<details>
<summary>Copying the files instead</summary>

```bash
mkdir -p ~/.claude/skills
cp -r skills/install-realityrouter ~/.claude/skills/
cp -r skills/connect-tool-to-realityrouter ~/.claude/skills/
```

Project-scoped instead of personal? Use `.claude/skills/` in the repo.
</details>

### Hermes, and anything reading well-known skill endpoints

realityrouter.dev serves the `/.well-known/skills/` convention, so these are
installable directly:

```bash
hermes skills inspect well-known:https://realityrouter.dev/.well-known/skills/install-realityrouter
hermes skills install well-known:https://realityrouter.dev/.well-known/skills/install-realityrouter
```

The endpoint is an index at `/.well-known/skills/index.json` plus a `SKILL.md`
per skill, served from this repository, so it never drifts from what is here.

Straight from GitHub works too, and `tap` keeps the repository as a source:

```bash
hermes skills tap add Lars-confi/RealityRouter
hermes skills install Lars-confi/RealityRouter/skills/install-realityrouter
```

**`connect-tool-to-realityrouter` will be blocked by Hermes's skill scanner**,
and it is worth knowing why before you `--force` it. The scanner flags any skill
that reads or writes another agent's configuration file as `persistence`:

```
CRITICAL persistence  references Hermes configuration files directly
                      ls ~/.hermes/config.yaml
HIGH     persistence  references other agent configuration files
                      ls ~/.codex/config.toml
HIGH     network      uses tunneling service for external access
                      cloudflared tunnel --url http://localhost:<port>
```

Editing those files is precisely what the skill is for — it points your tools at
the router — and the tunnel line is the documented way to reach the router from
Cursor. A scanner cannot tell that apart from malware by pattern alone. Run
`hermes skills inspect` first, read it, then install with `--force` if you are
satisfied. `install-realityrouter` scans clean and needs no flag.

### Everything else

The skills are plain Markdown with YAML frontmatter, and the content is
runtime-agnostic — only the file location and frontmatter convention differ.
Codex CLI, OpenCode, Cursor, Cline and Zed have no skill package format; they
read rules or instructions files. Point those at the canonical copies:

```
https://raw.githubusercontent.com/Lars-confi/RealityRouter/main/skills/install-realityrouter/SKILL.md
https://raw.githubusercontent.com/Lars-confi/RealityRouter/main/skills/connect-tool-to-realityrouter/SKILL.md
```

Fetching rather than copying means the instructions stay current as tool
configuration changes — which it does, frequently.

`https://realityrouter.dev/llms.txt` indexes both for agents that look there
first.

## Requirements

`install-realityrouter` uses the non-interactive commands on `start_router.py`
(`setup --non-interactive`, `--set`, `start --detach`, `status --json`). Without them the skill falls
back to handing off to the interactive wizard, which works but is not
unattended.
