import os
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from src.config.settings import get_settings
from src.main import app
from src.router.core import router_core
from tests.conftest import MockHTTPXResponse

client = TestClient(app)


def test_production_isolation_guard():
    assert "REALITY_ROUTER_HOME" in os.environ
    assert os.path.realpath(os.environ["REALITY_ROUTER_HOME"]) != os.path.realpath(
        os.path.expanduser("~/.reality_router")
    )


def test_local_reality_signal_readonly_smoke():
    for port in (18081, 18082):
        resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
        assert resp.status_code == 200
        assert resp.json().get("status") == "healthy"


def test_public_api_contract_with_local_reality_signal():
    settings = get_settings()
    settings.reality_routing_url = "http://127.0.0.1:18081"
    settings.reality_rerouting_url = "http://127.0.0.1:18082"
    settings.reality_check_token = "local_unauthenticated"
    settings.reality_check_provider = "Enterprise"

    router_core.models = {
        "gemini-2.5-flash": {
            "name": "Gemini 2.5 Flash",
            "cost": 0.001,
            "time": 1.0,
            "probability": 0.8,
            "supports_function_calling": True,
        }
    }
    mock_adapter = AsyncMock()
    mock_adapter.forward_request.return_value = {
        "text": "OK",
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }
    router_core.adapters = {"gemini-2.5-flash": mock_adapter}
    router_core.load_balancer.is_model_healthy = MagicMock(return_value=True)

    captured_urls = []

    async def mock_rc_post(url, json=None, headers=None, timeout=None, **kwargs):
        captured_urls.append(url)
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 101})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        assert client.get("/health").status_code == 200
        models_resp = client.get("/v1/models")
        assert models_resp.status_code == 200
        assert "data" in models_resp.json()

        settings.default_strategy = "expected_utility"
        snap_resp = client.post(
            "/v1/chat/completions",
            json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}]},
        )
        assert snap_resp.status_code == 200
        assert snap_resp.headers.get("X-RR-Model") == "gemini-2.5-flash"
        assert snap_resp.headers.get("X-RR-Strategy") == "expected_utility"

        settings.default_strategy = "tiered_assessment"
        ladder_resp = client.post(
            "/v1/chat/completions",
            json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}]},
        )
        assert ladder_resp.status_code == 200
        assert ladder_resp.headers.get("X-RR-Strategy") == "tiered_assessment"

        resp_api = client.post(
            "/v1/responses",
            json={"model": "gemini-2.5-flash", "input": "Hi"},
        )
        assert resp_api.status_code == 200
        assert client.get("/metrics/summary").status_code == 200

    assert any("127.0.0.1:18081/decide" in u for u in captured_urls)
    assert any("127.0.0.1:18082/decide" in u for u in captured_urls)

def test_tool_calling_streaming_and_endpoint_contracts():
    router_core.models = {
        "gemini-2.5-flash": {
            "name": "Gemini 2.5 Flash",
            "cost": 0.001,
            "time": 1.0,
            "probability": 0.8,
            "supports_function_calling": True,
        }
    }
    mock_adapter = AsyncMock()
    router_core.adapters = {"gemini-2.5-flash": mock_adapter}
    router_core.load_balancer.is_model_healthy = MagicMock(return_value=True)

    async def mock_rc_post(*args, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 102})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        # 1. Tool-calling contract on POST /v1/chat/completions
        mock_adapter.forward_request.return_value = {
            "text": "",
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": "{\"city\": \"Oslo\"}"},
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14},
        }
        tc_resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-2.5-flash",
                "messages": [{"role": "user", "content": "Weather in Oslo?"}],
                "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
            },
        )
        assert tc_resp.status_code == 200
        choice = tc_resp.json()["choices"][0]
        assert choice["finish_reason"] == "tool_calls"
        assert choice["message"]["tool_calls"][0]["function"]["name"] == "get_weather"

        # 2. Streaming SSE contract on POST /v1/chat/completions
        mock_adapter.forward_request.return_value = {
            "text": "Streamed hello",
            "usage": {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
        }
        stream_resp = client.post(
            "/v1/chat/completions",
            json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}], "stream": True},
        )
        assert stream_resp.status_code == 200
        assert "chat.completion.chunk" in stream_resp.text
        assert "data: [DONE]" in stream_resp.text

        # 3. Legacy POST /v1/completions contract
        cmpl_resp = client.post(
            "/v1/completions",
            json={"model": "gemini-2.5-flash", "prompt": "Say hello"},
        )
        assert cmpl_resp.status_code == 200
        assert cmpl_resp.json()["object"] == "text_completion"

        # 4. Anthropic POST /v1/messages contract (200 or graceful 501 fallback)
        msg_resp = client.post(
            "/v1/messages",
            json={"model": "gemini-2.5-flash", "max_tokens": 32, "messages": [{"role": "user", "content": "Hi"}]},
        )
        assert msg_resp.status_code in (200, 501)

        # 5. Agent discovery card contract
        card_resp = client.get("/.well-known/agent-card.json")
        assert card_resp.status_code == 200
        assert "capabilities" in card_resp.json()
        assert "endpoints" in card_resp.json()
