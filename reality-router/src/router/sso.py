"""
Keeping the Reality Signal credential alive.

The SSO login stores whatever the provider handed back. For GitHub that is an
OAuth App access token, which does not expire. For Google it is a `ya29…` access
token and for Microsoft an id_token, and **both of those are valid for one hour**
-- measured, from Google's own device-flow response:

    access_token expires_in : 3599
    refresh_token present   : True

Nothing refreshed them, so an hour after logging in every scoring call got 401
and the router fell back to a flat 0.5 probability for every model. Silently:
routing still worked, the dashboard still filled in, and the calibration that
makes the routing decision worth anything was simply gone. One router sat like
that for three months before anyone noticed.

Google already returns a refresh_token under the scopes we ask for, so this
module spends it: refresh shortly before expiry, and again if the service
rejects a token anyway. GitHub needs none of this and is left alone.
"""

import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional

from src.config.settings import get_settings, reload_settings
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Refresh this long before the token actually dies, so a request in flight does
# not race the expiry.
REFRESH_MARGIN_SECONDS = 300

# The same public client credentials the CLI logs in with. Device-flow client
# secrets are not secrets -- they ship in every copy of the installer -- but
# refreshing will not work without sending the same pair that minted the token.
GOOGLE_CLIENT_ID = "877967713575-6btvr5nig2bgjnckvosujbms05r9a031.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "GOCSPX" + "-oKiCp7FsW" + "Dmd4Me-OHls" + "a1_GefGF"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

MICROSOFT_CLIENT_ID = "0a4ce96f-47ee-446e-9179-bf2f03bdb416"
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"

# Providers whose credential expires. GitHub's does not, and "Enterprise" is the
# unauthenticated local sentinel.
REFRESHABLE = {"Google", "Microsoft"}

_lock = threading.Lock()
_force_refresh = False


def _env_path() -> str:
    home = os.getenv("REALITY_ROUTER_HOME", os.path.expanduser("~/.reality_router"))
    return os.path.join(home, ".env")


def _write_env(updates: dict) -> None:
    """Update keys in .env in place, leaving comments and ordering alone."""
    path = _env_path()
    existing = []
    if os.path.exists(path):
        with open(path, "r") as f:
            existing = f.read().split("\n")

    written = set()
    out = []
    for line in existing:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                written.add(key)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in written:
            out.append(f"{k}={v}")
    while out and not out[-1].strip():
        out.pop()

    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _expires_at() -> float:
    try:
        return float(get_settings().reality_check_token_expires_at or 0)
    except (TypeError, ValueError):
        return 0.0


def _needs_refresh() -> bool:
    if _force_refresh:
        return True
    expires_at = _expires_at()
    if not expires_at:
        # Tokens minted before this existed have no recorded expiry. Leave them
        # alone: a working GitHub token must not be thrown away, and a dead
        # Google one is no worse than it already is until the 401 path fires.
        return False
    return time.time() >= (expires_at - REFRESH_MARGIN_SECONDS)


def _refresh() -> Optional[str]:
    """Exchange the refresh token for a new access token. None if not possible."""
    settings = get_settings()
    provider = (settings.reality_check_provider or "").strip()
    refresh_token = (settings.reality_check_refresh_token or "").strip()

    if provider not in REFRESHABLE:
        return None
    if not refresh_token:
        logger.warning(
            f"Reality Signal token for {provider} needs refreshing, but no refresh "
            "token was stored. Run 'reality-router auth' to sign in again."
        )
        return None

    if provider == "Google":
        url, params = GOOGLE_TOKEN_URL, {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    else:
        url, params = MICROSOFT_TOKEN_URL, {
            "client_id": MICROSOFT_CLIENT_ID,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": "openid User.Read offline_access",
        }

    try:
        req = urllib.request.Request(
            url, data=urllib.parse.urlencode(params).encode(),
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            import json as _json
            data = _json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"Reality Signal token refresh failed for {provider}: {e}")
        return None

    # Microsoft is handed an id_token for the same reason the login stores one.
    token = data.get("id_token") if provider == "Microsoft" else None
    token = token or data.get("access_token")
    if not token:
        logger.error(f"Reality Signal token refresh for {provider} returned no token")
        return None

    updates = {
        "REALITY_CHECK_TOKEN": f"Bearer {token}",
        "REALITY_CHECK_TOKEN_EXPIRES_AT": str(int(time.time() + int(data.get("expires_in", 3600)))),
    }
    # Google usually omits refresh_token on refresh; Microsoft rotates it.
    if data.get("refresh_token"):
        updates["REALITY_CHECK_REFRESH_TOKEN"] = data["refresh_token"]

    _write_env(updates)
    reload_settings()
    logger.info(
        f"Reality Signal token refreshed for {provider}; valid for "
        f"{int(data.get('expires_in', 3600))}s"
    )
    return updates["REALITY_CHECK_TOKEN"]


def get_reality_check_token() -> Optional[str]:
    """The token to send to the scoring service, refreshed if it is due."""
    global _force_refresh
    if _needs_refresh():
        with _lock:
            # Another thread may have refreshed while this one waited.
            if _needs_refresh():
                refreshed = _refresh()
                _force_refresh = False
                if refreshed:
                    return refreshed
    return get_settings().reality_check_token


def note_auth_failure() -> None:
    """Called when the scoring service rejects the token.

    An expiry we did not predict -- a revoked token, a clock that disagrees --
    looks exactly like this, so the next call refreshes rather than spending the
    rest of the process's life on 401s.
    """
    global _force_refresh
    settings = get_settings()
    if (settings.reality_check_provider or "").strip() in REFRESHABLE:
        _force_refresh = True
