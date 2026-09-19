import base64
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.router import auth

KEY = "k" * 40


def _app():
    app = FastAPI()
    app.middleware("http")(auth.require_api_key)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/v1/chat/completions")
    async def chat():
        return {"ok": True}

    @app.post("/v1/messages")
    async def messages():
        return {"ok": True}

    @app.get("/metrics/dashboard")
    async def dashboard():
        return {"ok": True}

    return TestClient(app)


@pytest.fixture
def keys():
    configured = []
    with patch.object(
        auth, "get_settings", lambda: SimpleNamespace(router_api_keys=configured)
    ):
        yield configured


def test_auth_off_when_no_keys_configured(keys):
    assert _app().post("/v1/chat/completions").status_code == 200


def test_missing_key_is_rejected_with_openai_error(keys):
    keys.append(KEY)
    r = _app().post("/v1/chat/completions")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_api_key"


def test_wrong_key_is_rejected(keys):
    keys.append(KEY)
    r = _app().post("/v1/chat/completions", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_bearer_key_is_accepted(keys):
    keys.append(KEY)
    r = _app().post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 200


def test_any_configured_key_is_accepted(keys):
    keys.extend(["a" * 40, KEY])
    r = _app().post("/v1/chat/completions", headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 200


def test_x_api_key_is_accepted_and_messages_gets_anthropic_error(keys):
    keys.append(KEY)
    client = _app()
    assert client.post("/v1/messages", headers={"x-api-key": KEY}).status_code == 200
    r = client.post("/v1/messages")
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"


def test_dashboard_prompts_for_basic_auth_and_accepts_it(keys):
    keys.append(KEY)
    client = _app()
    r = client.get("/metrics/dashboard")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")
    basic = base64.b64encode(f"any-user:{KEY}".encode()).decode()
    assert client.get("/metrics/dashboard", headers={"Authorization": f"Basic {basic}"}).status_code == 200


def test_health_stays_public(keys):
    keys.append(KEY)
    assert _app().get("/health").status_code == 200


def test_cors_preflight_is_not_blocked(keys):
    keys.append(KEY)
    r = _app().options("/v1/chat/completions")
    assert r.status_code != 401
