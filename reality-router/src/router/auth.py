"""
Inbound API-key authentication.

Off unless ROUTER_API_KEYS is set in the router's .env, so a router on
localhost behaves exactly as before. Turn it on before the router is reachable
from anywhere you do not control -- a public tunnel, for example, which is the
only way Cursor can reach it, because Cursor sends requests from its own cloud.
Without it, anyone who finds the address can spend the provider keys.

A request authenticates with any one of the configured keys, sent as:
  - Authorization: Bearer <key>   (OpenAI SDKs, Cursor, most clients)
  - x-api-key: <key>              (Anthropic clients, e.g. Claude Code)
  - HTTP Basic, key as password   (browsers, for the dashboard)

The client's key is never forwarded to a provider: providers are always called
with the router's own keys.
"""

import base64
import hmac
from typing import List

from fastapi import Request
from fastapi.responses import JSONResponse

from src.config.settings import get_settings
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Liveness and discovery stay open, so health checks and agent discovery work
# without a key. Nothing here reveals configuration or spends credits.
PUBLIC_PATHS = {
    "/",
    "/health",
    "/.well-known/agent-card.json",
    "/v1/.well-known/agent-card.json",
}


def presented_keys(request: Request) -> List[str]:
    """Every credential the request carries, in any supported form."""
    keys = []
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    scheme, value = scheme.lower(), value.strip()
    if scheme == "bearer" and value:
        keys.append(value)
    elif scheme == "basic" and value:
        try:
            _, _, password = base64.b64decode(value).decode("utf-8").partition(":")
        except Exception:
            password = ""
        if password:
            keys.append(password)
    api_key = request.headers.get("x-api-key", "").strip()
    if api_key:
        keys.append(api_key)
    return keys


def is_authorized(presented: List[str], configured: List[str]) -> bool:
    # compare_digest on every pair, so timing does not reveal a partial match
    return any(
        hmac.compare_digest(p.encode("utf-8"), k.encode("utf-8"))
        for p in presented
        for k in configured
    )


def _unauthorized(path: str) -> JSONResponse:
    message = (
        "Invalid or missing API key. This router requires one of the keys in "
        "its ROUTER_API_KEYS setting."
    )
    if path.startswith("/v1/messages"):
        # Anthropic error shape, so Anthropic clients report it properly
        return JSONResponse(
            status_code=401,
            content={
                "type": "error",
                "error": {"type": "authentication_error", "message": message},
            },
        )
    if path.startswith("/v1/"):
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "message": message,
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
            },
        )
    # Dashboard and metrics: let the browser prompt for the key
    return JSONResponse(
        status_code=401,
        content={"detail": message},
        headers={"WWW-Authenticate": 'Basic realm="Reality Router"'},
    )


async def require_api_key(request: Request, call_next):
    configured = get_settings().router_api_keys
    if (
        not configured
        or request.method == "OPTIONS"  # CORS preflight carries no credentials
        or request.url.path in PUBLIC_PATHS
    ):
        return await call_next(request)

    if is_authorized(presented_keys(request), configured):
        return await call_next(request)

    client = request.client.host if request.client else "unknown"
    logger.warning(f"Rejected unauthenticated request: {request.method} {request.url.path} from {client}")
    return _unauthorized(request.url.path)


def log_auth_status() -> None:
    configured = get_settings().router_api_keys
    if not configured:
        logger.info(
            "Inbound API-key auth is off (ROUTER_API_KEYS not set). "
            "Keep the router on localhost or a private network."
        )
        return
    logger.info(f"Inbound API-key auth is on ({len(configured)} key(s) configured).")
    if any(len(k) < 24 for k in configured):
        logger.warning(
            "A key in ROUTER_API_KEYS is shorter than 24 characters. Use a long "
            "random key, e.g. the output of: openssl rand -hex 32"
        )
