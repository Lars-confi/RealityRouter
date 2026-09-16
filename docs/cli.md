# Command-Line Interface Reference (`cli.md`)

This is the authoritative command reference for `reality-router` CLI. It supports zero-prompt automated workflows for agents and headless systems, alongside interactive user terminal environments.

---

## Global Precedence Rule

When configuring RealityRouter settings, values are resolved using the following priority (highest priority first):
```
CLI Flags > Process Environment Variables > Config File (.env) > Auto-detect > Fallbacks / Interactive prompts
```

---

## Global Options

These flags modify CLI behavior across multiple commands:

- `--agent`: **Agent / Automation mode.** Bypasses TUI prompt loops, selects sensible defaults, automatically registers temporary tokens if missing, and auto-detects environment settings.
- `--non-interactive` / `--headless`: **Crash on missing values.** In this mode, if any required parameter is not available in environment or configuration, the CLI exits with a non-zero code instead of prompting.
- `--json`: **Machine-readable output.** Emits newline-delimited JSON (NDJSON) events or serialized output rather than ANSI colored text.
- `--port <integer>`: Bind to this port. If the port is busy, the start command fails.
- `--detach`: Start the router process in the background and return control once the `/health` endpoint is verified as healthy.

---

## Subcommand Directory

RealityRouter is controlled using a command-oriented syntax:
```bash
reality-router <command> [options]
```

### 1. `setup`
Configures system environment and credentials.
- **Usage**: `reality-router setup [--agent] [--non-interactive]`
- **Interactive TUI**: Leads you through step-by-step questions to authenticate with Reality Signal, configure provider API keys, and choose routing strategies.
- **Zero-Prompt Mode (`--agent`)**: Auto-detects local keys, configures defaults, and skips interactive prompts.

### 2. `start`
Launches the RealityRouter core proxy server.
- **Usage**: `reality-router start [--port <int>] [--detach] [--json]`
- **Behavior**:
  - Validates keys and SSO status.
  - If `--port` is omitted, searches for the next available port starting from `8000`.
  - If `--detach` is specified, runs uvicorn in a separate background session and writes port/pid daemon state files under `~/.reality_router/`.

### 3. `stop`
Stops background daemonized instances.
- **Usage**: `reality-router stop [--json]`
- **Behavior**: Reads `~/.reality_router/router.pid`, gracefully terminates the process via `SIGTERM` (falling back to `SIGKILL` if unresponsive), and cleans up daemon state files.

### 4. `status`
Inspects the current execution state of the proxy.
- **Usage**: `reality-router status [--json]`
- **Human-Readable Output**: Lists PID, Port, Base URL, Dashboard URL, Active Strategy, and active providers.
- **JSON Output (`--json`)**: Returns clean metadata on the active instance port/urls:
  ```json
  {
    "status": "running",
    "pid": 12345,
    "port": 8001,
    "url": "http://localhost:8001",
    "base_url": "http://localhost:8001/v1",
    "dashboard_url": "http://localhost:8001/metrics/dashboard"
  }
  ```

### 5. `doctor`
Runs system diagnostics and checks health of connected adapters.
- **Usage**: `reality-router doctor [--json]`
- **Checks**:
  - Presence and permissions of `.env` file.
  - Validity of SSO `REALITY_CHECK_TOKEN`.
  - Active provider keys.
  - Connection status to local Ollama endpoints.
  - Port availability.
- **Exit Codes**:
  - `0`: Success / All clear / Healthy (`EXIT_OK`).
  - `2`: Invalid CLI arguments or usage error (`EXIT_USAGE`).
  - `3`: Missing model provider credentials or auth token (`EXIT_NO_CREDENTIALS`).
  - `4`: Credentials present but no models discovered (`EXIT_NO_MODELS`).
  - `5`: Server started but unhealthy or /health failed (`EXIT_UNHEALTHY`).
  - `6`: Binding port busy (`EXIT_PORT_BUSY`).
  - `7`: Router already running (`EXIT_ALREADY_RUNNING`).

### 6. `models`
Inspects and manages candidate models available for routing.
- **Usage**: `reality-router models [--disable-model <id>] [--enable-model <id>] [--enable-all-models] [--json]`
- **Features**:
  - List all discovered models across active providers.
  - Manually disable model IDs (persisted to `disabled_models.json`).
  - Re-enable or enable all discovered model options.

### 7. `auth`
Authenticates the CLI workspace with the Reality Signal calibration network.
- **Usage**: `reality-router auth [--auth <token>] [--agent] [--json]`
- **Authentication Flows**:
  - If `--auth <token>` is passed, configures the token directly.
  - In `--agent` or interactive mode, initiates a Device Authorization Flow (OAuth 2.0) with Microsoft, GitHub, or Google. Emits NDJSON verification URI and user code for machine automated completion.

---

## Custom Parameter Setting Flags

These specific parameter adjustments can be passed directly to subcommands or run on their own:
- `--strategy <expected_utility | tiered_assessment>`: Sets the routing strategy.
- `--cost-sensitivity <alpha>`: Multiplier for cost weights (alpha) in the expected utility formula.
- `--time-sensitivity <beta>`: Multiplier for latency weights (beta) in the expected utility formula.
- `--sentiment-model <model-id>`: Specific model ID to use for the sentiment feedback loop.
- `--ollama-url <url>`: Configures a custom Ollama local network URL.
- `--reality-routing-url <url>`: Sets custom Snap expected-utility calibration remote API.
- `--reality-rerouting-url <url>`: Sets custom Ladder sequential escalation remote API.
- `--set "KEY=VALUE"`: Writes a configuration parameter directly to `~/.reality_router/.env` and exits.
