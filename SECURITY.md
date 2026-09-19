# Security Policy & Guidelines (SECURITY.md)

This document describes the security model, configuration guidelines, and procedures for reporting security vulnerabilities in RealityRouter.

## Supported Versions

Only the latest commit/version on the `main` branch of the RealityRouter repository is actively supported with security patches.

| Version | Supported |
| :--- | :--- |
| Core / main | Yes |
| Pre-release / older tags | No |

## Responsible Vulnerability Disclosure

If you find any security vulnerability in RealityRouter (including but not limited to leaks, privilege escalations, or remote execution risks), please report it responsibly by opening a private vulnerability report or a confidential ticket through the project repository's private hosting channels on Forgejo.

Please do not open a public GitHub/Gitea issue for unresolved security issues.

---

## 1. Ingress Security & Network Exposure

RealityRouter serves as an API proxy. **By default it accepts any request**, so any placeholder key works for local access. Setting `ROUTER_API_KEYS` in `~/.reality_router/.env` turns on API-key authentication: every request except `/health` must then carry one of the configured keys (see section 2).

### ⚠️ Critical Ingress Warning
> **DO NOT EXPOSE THE REALITYROUTER HTTP PORT TO AN UNTRUSTED OR PUBLIC NETWORK WITHOUT `ROUTER_API_KEYS` SET.**
> By default, RealityRouter bindings (e.g. `0.0.0.0:8000`) allow connections from external hosts. If exposed without restriction, malicious clients could use your router to dispatch models, consuming your configured API credentials and incurring major costs.

### Hardening Recommendations:
- **Keep on localhost**: Bind the server explicitly to localhost (`127.0.0.1`) if only local tools (e.g., Cline, Aider, Claude Code) are using it.
- **Private Network**: Keep RealityRouter inside a restricted private network (e.g., Tailscale, private subnet, or VPC) where only approved clients have route access.
- **API keys before exposure**: If the router must be reachable from outside — a public tunnel for Cursor, for example, whose requests come from Cursor's cloud — set `ROUTER_API_KEYS` first and only expose it over HTTPS.
- **Reverse Proxy / VPN**: For anything beyond key authentication (rate limits, SSO, IP allow-lists), put RealityRouter behind a reverse proxy (like Nginx or Caddy) with TLS enabled.

---

## 2. Ingress & Dashboard Authentication Status

- **Off by default**: with `ROUTER_API_KEYS` unset, the API and the dashboard are unauthenticated. Keep the router on localhost or a private network.
- **On with `ROUTER_API_KEYS`**: a comma-separated list of keys. Every path except `/`, `/health` and the agent card requires one, sent as `Authorization: Bearer <key>`, as `x-api-key: <key>`, or as HTTP Basic with the key as the password. The dashboard uses Basic, so the browser shows a login prompt. Keys are compared in constant time, and failed attempts are logged with the client address but never the presented key.
- **Keys stay at the router**: a client's key is not forwarded to any provider. Providers are always called with the router's own credentials.
- **Use long random keys** (`openssl rand -hex 32`). The router warns at startup if a key is shorter than 24 characters. Use one key per person or tool so each can be revoked by removing it and restarting.

---

## 3. Secrets Storage & File Permissions

All API keys and credentials are saved in the environment configuration file:
```
~/.reality_router/.env
```
- **File Permissions**: The system attempts to write `.env` with restricted permissions (e.g., `600` or `700` user-only read/write). On multi-user hosts, verify that this directory is not readable by other users.
- **Secrets Redaction**: RealityRouter implements automated logger redaction. API keys, SSO tokens, and raw credentials are never printed to `stdout`, `stderr`, or written to `server.log`. Under `--json` output, credential values are replaced with placeholder hashes or hidden entirely.

---

## 4. Multi-User Host Considerations

Since local sockets are shared on multi-user operating systems, multiple users can access standard local ports (e.g., `8000`). If hosting on a shared server:
- Run RealityRouter in an isolated network namespace.
- Restrict folder read/write permissions on `~/.reality_router`.
- Use a distinct port per user or deploy via isolated Docker containers.

---

## 5. Docker Considerations

When deploying via the `Dockerfile` or `compose.yaml`:
- The container binds to host `0.0.0.0` by default.
- Never map port `8000` to a public interface unless a reverse proxy manages authorization.
- Mount `~/.reality_router` as a restricted user-only volume to keep secrets safe.
