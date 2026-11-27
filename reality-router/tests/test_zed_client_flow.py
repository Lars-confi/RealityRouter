import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.router.core import router_core

client = TestClient(app)


class MockHTTPXResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)

    def json(self):
        return self._json_data


def _parse_chat_sse(raw_text: str):
    chunks = []
    done = False
    for line in raw_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            done = True
            continue
        chunks.append(json.loads(payload))
    return chunks, done


@pytest.fixture(autouse=True)
def restore_router_state():
    saved_models = dict(router_core.models)
    saved_adapters = dict(router_core.adapters)
    saved_lb = router_core.load_balancer.is_model_healthy
    saved_sessions = dict(router_core.active_sessions)
    try:
        yield
    finally:
        router_core.models = saved_models
        router_core.adapters = saved_adapters
        router_core.load_balancer.is_model_healthy = saved_lb
        router_core.active_sessions.clear()
        router_core.active_sessions.update(saved_sessions)


def _setup_zed_router(turn_responses):
    router_core.models = {
        "qwen3-coder:30b": {
            "name": "Qwen 3 Coder 30B",
            "cost": 0.001,
            "time": 1.0,
            "probability": 0.95,
            "supports_function_calling": True,
        }
    }
    adapter = AsyncMock()
    adapter.forward_request = AsyncMock(side_effect=list(turn_responses))
    router_core.adapters = {"qwen3-coder:30b": adapter}
    router_core.load_balancer.is_model_healthy = MagicMock(return_value=True)
    return adapter


def test_zed_multiturn_streaming_tool_call_and_metrics_attribution():
    """1. Zed multi-turn stream: tool_calls delta -> tool result -> text + /metrics/summary attribution."""
    _setup_zed_router([
        {
            "text": "",
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_zed_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
        },
        {
            "text": "README explains RealityRouter.",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 45, "completion_tokens": 10, "total_tokens": 55},
        },
    ])
    assert client.post("/metrics/reset").status_code == 200
    user_agent = "Zed/0.180.0"

    async def mock_rc_post(*args, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.95, "decision_id": 100})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        resp1 = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer rr-local", "User-Agent": user_agent},
            json={
                "model": "auto",
                "stream": True,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "description": "Read a file",
                            "parameters": {
                                "type": "object",
                                "properties": {"path": {"type": "string"}},
                                "required": ["path"],
                            },
                        },
                    }
                ],
                "messages": [{"role": "user", "content": "Read README.md for me"}],
            },
        )
        assert resp1.status_code == 200
        assert resp1.headers["content-type"].startswith("text/event-stream")
        chunks1, done1 = _parse_chat_sse(resp1.text)
        assert done1 is True
        tool_deltas = [
            tc
            for c in chunks1
            for ch in c.get("choices", [])
            for tc in (ch.get("delta", {}).get("tool_calls") or [])
        ]
        assert tool_deltas, f"Expected tool_calls delta in {chunks1}"
        assert tool_deltas[0]["function"]["name"] == "read_file"
        assert tool_deltas[0]["function"]["arguments"] == '{"path":"README.md"}'
        assert any(
            ch.get("finish_reason") == "tool_calls"
            for c in chunks1
            for ch in c.get("choices", [])
        )

        resp2 = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer rr-local", "User-Agent": user_agent},
            json={
                "model": "auto",
                "stream": True,
                "messages": [
                    {"role": "user", "content": "Read README.md for me"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_zed_1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"README.md"}',
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_zed_1",
                        "content": "# RealityRouter",
                    },
                ],
            },
        )
        assert resp2.status_code == 200
        assert resp2.headers["content-type"].startswith("text/event-stream")
        chunks2, done2 = _parse_chat_sse(resp2.text)
        assert done2 is True
        combined_text = "".join(
            ch.get("delta", {}).get("content") or ""
            for c in chunks2
            for ch in c.get("choices", [])
        )
        assert "README explains RealityRouter." in combined_text
        assert any(
            ch.get("finish_reason") == "stop"
            for c in chunks2
            for ch in c.get("choices", [])
        )

    summary_resp = client.get("/metrics/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert user_agent in summary["agents"]
    assert summary["agents"][user_agent]["requests"] == 2


def test_zed_session_synthesizes_consistent_agent_trajectory():
    """2. Zed multi-turn trajectory preserves Zed/0.180.0 attribution in /metrics/history."""
    _setup_zed_router([
        {
            "text": "",
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_z_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
                }
            ],
            "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
        },
        {
            "text": "Here is the README content.",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 40, "completion_tokens": 6, "total_tokens": 46},
        },
    ])
    assert client.post("/metrics/reset").status_code == 200
    user_agent = "Zed/0.180.0"

    async def mock_rc_post(*args, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.95, "decision_id": 200})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        r1 = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer rr-local", "User-Agent": user_agent},
            json={
                "model": "auto",
                "stream": True,
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "read_file", "parameters": {"type": "object"}},
                    }
                ],
                "messages": [{"role": "user", "content": "Read README.md"}],
            },
        )
        assert r1.status_code == 200

        r2 = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer rr-local", "User-Agent": user_agent},
            json={
                "model": "auto",
                "stream": True,
                "messages": [
                    {"role": "user", "content": "Read README.md"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_z_1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"README.md"}',
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_z_1", "content": "# RealityRouter"},
                ],
            },
        )
        assert r2.status_code == 200

    history_resp = client.get("/metrics/history")
    assert history_resp.status_code == 200
    history_items = history_resp.json()
    assert isinstance(history_items, list)
    zed_logs = [h for h in history_items if h.get("agent_id") == user_agent]
    assert len(zed_logs) == 2
