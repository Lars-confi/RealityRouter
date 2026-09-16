# Security Policy & Guidelines (SECURITY.md)

This document describes the security model, configuration guidelines, and procedures for reporting security vulnerabilities in RealityRouter.

## Supported Versions

Only the latest commit/version on the `main` branch of the RealityRouter repository is actively supported with security patches.

| Version | Supported |
| :--- | :--- |
| Core / main | Yes |
| Pre-release / older tags | No |

## Responsible Vulnerability Disclosure

If you find any security vulnerability in RealityRouter (including but not limited to leaks, privilege escalations, or remote execution risks), please report it responsibly by opening a confidential inquiry or emailing the maintainers directly at `security@realityrouter.local` (or via private channels on the project repository hosting site).

Please do not open a public GitHub/Gitea issue for unresolved security issues.

---

## 1. Ingress Security & Network Exposure

RealityRouter serves as an API proxy. Currently, **local HTTP ingress does not perform active cryptographic signature or token validation** of incoming requests. The `Authorization` header is accepted, but any placeholder key is permitted for local access.

### ⚠️ Critical Ingress Warning
> **DO NOT EXPOSE THE REALITYROUTER HTTP PORT DIRECTLY TO AN UNTRUSTED OR PUBLIC NETWORK.**
> By default, RealityRouter bindings (e.g. `0.0.0.0:8000`) allow connections from external hosts. If exposed without restriction, malicious clients could use your router to dispatch models, consuming your configured API credentials and incurring major costs.

### Hardening Recommendations:
- **Keep on localhost**: Bind the server explicitly to localhost (`127.0.0.1`) if only local tools (e.g., Cline, Aider, Claude Code) are using it.
- **Private Network**: Keep RealityRouter inside a restricted private network (e.g., Tailscale, private subnet, or VPC) where only approved clients have route access.
- **Reverse Proxy / VPN**: Put RealityRouter behind an authenticated reverse proxy (like Nginx, Caddy, or Apache) with TLS enabled and active Basic/Bearer authentication to validate incoming clients.

---

## 2. Ingress & Dashboard Authentication Status

- **API Ingress Authentication**: Accepts `Authorization` header placeholders but does not cryptographically enforce a secret key by default in local single-user mode.
- **Dashboard Authentication**: The developer metrics dashboard (`/metrics/dashboard`) is unauthenticated. Ensure it is protected using private network configurations or basic-auth reverse proxies.

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
