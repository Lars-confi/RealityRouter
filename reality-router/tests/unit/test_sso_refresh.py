import json
import os
import time
from io import BytesIO
from unittest.mock import patch

import pytest

from src.router import sso


def write_env(**values):
    path = sso._env_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for k, v in values.items():
            f.write(f"{k}={v}\n")
    from src.config.settings import reload_settings
    reload_settings()


def read_env():
    path = sso._env_path()
    out = {}
    with open(path) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                out[k] = v
    return out


def fake_token_response(**extra):
    body = {"access_token": "new-access-token", "expires_in": 3599, **extra}
    return BytesIO(json.dumps(body).encode())


@pytest.fixture(autouse=True)
def reset_state():
    sso._force_refresh = False
    yield
    sso._force_refresh = False


def test_valid_token_is_not_refreshed():
    write_env(
        REALITY_CHECK_TOKEN="Bearer still-good",
        REALITY_CHECK_PROVIDER="Google",
        REALITY_CHECK_REFRESH_TOKEN="r",
        REALITY_CHECK_TOKEN_EXPIRES_AT=str(int(time.time() + 3600)),
    )
    with patch("urllib.request.urlopen") as m:
        assert sso.get_reality_check_token() == "Bearer still-good"
        m.assert_not_called()


def test_expired_google_token_is_refreshed_and_persisted():
    write_env(
        REALITY_CHECK_TOKEN="Bearer expired",
        REALITY_CHECK_PROVIDER="Google",
        REALITY_CHECK_REFRESH_TOKEN="the-refresh-token",
        REALITY_CHECK_TOKEN_EXPIRES_AT=str(int(time.time() - 10)),
    )
    with patch("urllib.request.urlopen", return_value=fake_token_response()) as m:
        assert sso.get_reality_check_token() == "Bearer new-access-token"
        assert m.call_count == 1

    env = read_env()
    assert env["REALITY_CHECK_TOKEN"] == "Bearer new-access-token"
    # the new expiry is recorded, so the next hour is handled too
    assert int(env["REALITY_CHECK_TOKEN_EXPIRES_AT"]) > time.time() + 3000
    # Google omits refresh_token on refresh; the original must survive
    assert env["REALITY_CHECK_REFRESH_TOKEN"] == "the-refresh-token"


def test_token_near_expiry_is_refreshed_before_it_dies():
    write_env(
        REALITY_CHECK_TOKEN="Bearer nearly-done",
        REALITY_CHECK_PROVIDER="Google",
        REALITY_CHECK_REFRESH_TOKEN="r",
        REALITY_CHECK_TOKEN_EXPIRES_AT=str(int(time.time() + 60)),
    )
    with patch("urllib.request.urlopen", return_value=fake_token_response()):
        assert sso.get_reality_check_token() == "Bearer new-access-token"


def test_rotated_refresh_token_is_stored():
    write_env(
        REALITY_CHECK_TOKEN="Bearer expired",
        REALITY_CHECK_PROVIDER="Microsoft",
        REALITY_CHECK_REFRESH_TOKEN="old-refresh",
        REALITY_CHECK_TOKEN_EXPIRES_AT=str(int(time.time() - 10)),
    )
    resp = fake_token_response(refresh_token="rotated-refresh", id_token="an-id-token")
    with patch("urllib.request.urlopen", return_value=resp):
        # Microsoft is sent the id_token, as the login stores
        assert sso.get_reality_check_token() == "Bearer an-id-token"
    assert read_env()["REALITY_CHECK_REFRESH_TOKEN"] == "rotated-refresh"


def test_github_token_is_left_alone():
    write_env(
        REALITY_CHECK_TOKEN="Bearer gho_something",
        REALITY_CHECK_PROVIDER="GitHub",
    )
    with patch("urllib.request.urlopen") as m:
        assert sso.get_reality_check_token() == "Bearer gho_something"
        m.assert_not_called()


def test_token_without_recorded_expiry_is_not_touched():
    # Tokens minted before expiries were tracked: leave them, the 401 path
    # handles them rather than a refresh we cannot make.
    write_env(
        REALITY_CHECK_TOKEN="Bearer legacy",
        REALITY_CHECK_PROVIDER="Google",
    )
    with patch("urllib.request.urlopen") as m:
        assert sso.get_reality_check_token() == "Bearer legacy"
        m.assert_not_called()


def test_auth_failure_forces_a_refresh_on_the_next_call():
    write_env(
        REALITY_CHECK_TOKEN="Bearer rejected",
        REALITY_CHECK_PROVIDER="Google",
        REALITY_CHECK_REFRESH_TOKEN="r",
        REALITY_CHECK_TOKEN_EXPIRES_AT=str(int(time.time() + 3600)),
    )
    sso.note_auth_failure()
    with patch("urllib.request.urlopen", return_value=fake_token_response()) as m:
        assert sso.get_reality_check_token() == "Bearer new-access-token"
        assert m.call_count == 1


def test_auth_failure_is_ignored_for_github():
    write_env(
        REALITY_CHECK_TOKEN="Bearer gho_something",
        REALITY_CHECK_PROVIDER="GitHub",
    )
    sso.note_auth_failure()
    with patch("urllib.request.urlopen") as m:
        assert sso.get_reality_check_token() == "Bearer gho_something"
        m.assert_not_called()


def test_failed_refresh_keeps_the_old_token_rather_than_none():
    write_env(
        REALITY_CHECK_TOKEN="Bearer expired",
        REALITY_CHECK_PROVIDER="Google",
        REALITY_CHECK_REFRESH_TOKEN="r",
        REALITY_CHECK_TOKEN_EXPIRES_AT=str(int(time.time() - 10)),
    )
    with patch("urllib.request.urlopen", side_effect=Exception("network down")):
        # A failed refresh must not take the router down; the call still gets a
        # token and the scoring service decides whether it is good.
        assert sso.get_reality_check_token() == "Bearer expired"


def test_missing_refresh_token_does_not_call_the_provider():
    write_env(
        REALITY_CHECK_TOKEN="Bearer expired",
        REALITY_CHECK_PROVIDER="Google",
        REALITY_CHECK_TOKEN_EXPIRES_AT=str(int(time.time() - 10)),
    )
    with patch("urllib.request.urlopen") as m:
        assert sso.get_reality_check_token() == "Bearer expired"
        m.assert_not_called()
