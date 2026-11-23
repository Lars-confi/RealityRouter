"""Deterministic offline HTTP public API & SSE streaming contract tests."""
import json
import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, MetaData

from src.main import app
from src.models.database import Base
from src.models.routing import RoutingResponse
from src.router.core import router_core
from src.router.metrics import RoutingLog
from src.utils.capability_tester import capability_manager

# ---------------------------------------------------------------------
# Mock helper - returns a real RoutingResponse instance
# ---------------------------------------------------------------------
def _make_routing_response(
    *,
    response_text="OK",
    usage=None,
    tool_calls=None,
    finish_reason="end",  # used by non‑streaming responses
    model_id="mock-model-id",
    model_name="MockModel",
    expected_utility=0.9,
    cost=0.001,
    time=0.2,
    probability=0.95,
    extra_response_json=None,
):
    """
    Construct a fully‑populated RoutingResponse object.
    The router code expects ``response`` to be a dict with at least
    ``text`` and optionally ``usage`` and ``tool_calls``.
    """
    resp_dict = {
        "text": response_text,
        "finish_reason": finish_reason,
    }
    if usage:
        resp_dict["usage"] = usage.copy()
    if tool_calls:
        resp_dict["tool_calls"] = tool_calls
    if extra_response_json:
        resp_dict.update(extra_response_json)

    # Build the response payload the router will return
    payload = {
        "agent_id": "default",
        "model_id": model_id,
        "model_name": model_name,
        "expected_utility": expected_utility,
        "cost": cost,
        "time": time,
        "probability": probability,
        "timestamp": None,
        "response": resp_dict,
    }

    # Return a *real* RoutingResponse instance – the router code will treat it
    # as a pydantic model and access its attributes.
    return RoutingResponse(**payload)


# ---------------------------------------------------------------------
# 1. POST /v1/messages (Anthropic Messages API)
# ---------------------------------------------------------------------
def test_messages_non_streaming_text():
    with patch.object(
        router_core, "route_request", new=AsyncMock(return_value=_make_routing_response(
            response_text="Hello World",
            extra_response_json={
                "content": {"type": "text", "text": "Hello World"},
                "usage": {"prompt_tokens": 5, "completion_tokens": 9}
            }
        ))
    ):
        client = TestClient(app)
        resp = client.post(
            "/v1/messages",
            json={
                "role": "user",
                "content": "Say hello",
                "stream": False,
            },
        )
        assert resp.status_code == 200
        # Custom routing headers
        assert resp.headers.get("x-rr-model") == "mock-model-id"
        assert resp.headers.get("x-rr-strategy") is not None
        assert float(resp.headers.get("x-rr-cost", "0")) >= 0
        assert float(resp.headers.get("x-rr-saved", "0")) >= 0
        data = resp.json()
        assert isinstance(data["content"], list)
        assert any(isinstance(b, dict) and b.get("type") == "text" for b in data["content"])
        assert data["content"][0]["text"] == "Hello World"
        assert data["stop_reason"] == "end_turn"


def test_messages_tools_roundtrip():
    tool_call = [{
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "my_tool", "arguments": '{"foo": "bar"}'}
    }]
    with patch.object(
        router_core, "route_request", new=AsyncMock(return_value=_make_routing_response(
            extra_response_json={
                "content": [{
                    "type": "tool_use",
                    "tool_name": "my_tool",
                    "input_schema": {"type": "object", "properties": {"foo": {"type": "string"}}},
                    "tool_input": {"foo": "bar"},
                    "stop_reason": "tool_use"
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}
            }
        ))
    ):
        client = TestClient(app)
        resp = client.post(
            "/v1/messages",
            json={
                "role": "user",
                "content": "Do a thing",
                "stream": False,
                "tools": [{"name": "my_tool", "description": "d", "input_schema": {"type": "object", "properties": {"foo": {"type": "string"}}}}],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        tool_blocks = [c for c in data["content"] if c.get("type") == "tool_use"]
        assert len(tool_blocks) == 1
        tb = tool_blocks[0]
        assert tb["tool_name"] == "my_tool"
        assert tb["stop_reason"] == "tool_use"


def test_messages_streaming_sse():
    streaming_payload = {
        "content": [
            {"type": "text", "text": "Chunk 1"},
            {"type": "text", "text": "Chunk 2"}
        ],
    }
    # Build the SSE frames the endpoint will emit
    sse_frames = []
    for block in streaming_payload["content"]:
        sse_frames.append(json.dumps({
            "event": "content_block_delta",
            "data": {"delta": {"content": block["text"]}}
        }))
    sse_frames.append(json.dumps({"event": "message_stop"}))

    async def stream_generator():
        for frame in sse_frames:
            yield frame

    with patch.object(
        router_core, "route_request", new=AsyncMock(return_value=_make_routing_response(
            extra_response_json={"content": streaming_payload}
        ))
    ):
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            with patch("src.router.core.asyncio.sleep", new=AsyncMock()):
                client = TestClient(app)
                resp = client.post(
                    "/v1/messages?stream=true", json={
                        "role": "user", "content": "Stream me"
                    }, params={"stream": True}
                )
                assert resp.status_code == 200
                assert resp.headers.get("content-type", "").startswith("text/event-stream")
                lines = [l.strip() for l in resp.iter_lines() if l.strip().startswith("data:")]
                payloads = [json.loads(l.split("data: ", 1)[1]) for l in lines]
                assert any(p.get("event") == "message_stop" for p in payloads)


# ---------------------------------------------------------------------
# 2. POST /v1/chat/completions (stream=True)
# ---------------------------------------------------------------------
def test_chat_completions_streaming_text():
    with patch.object(
        router_core, "route_request", new=AsyncMock(return_value=_make_routing_response(
            usage={"prompt_tokens": 5, "completion_tokens": 7}
        ))
    ):
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            with patch("src.router.core.asyncio.sleep", new=AsyncMock()):
                client = TestClient(app)
                resp = client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Hello"}], "stream": True},
                )
                assert resp.status_code == 200
                assert resp.headers.get("content-type", "").startswith("text/event-stream")
                lines = [l.strip() for l in resp.iter_lines() if l.strip().startswith("data:")]
                chunk_line = next((l for l in lines if "object: chat.completion.chunk" in l), None)
                assert chunk_line is not None
                delta_json = json.loads(chunk_line.split("data: ", 1)[1])
                assert isinstance(delta_json, dict)
                assert "delta" in delta_json
                assert "content" in delta_json["delta"]
                assert "usage" in delta_json["delta"]
                done_line = next((l for l in lines if "[DONE]" in l), None)
                assert done_line is not None


def test_chat_completions_streaming_tool_calls():
    tool_call = [{
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "my_tool", "arguments": '{"x": 1}'}
    }]
    with patch.object(
        router_core, "route_request", new=AsyncMock(return_value=_make_routing_response(
            json_data={
                "choices": [{
                    "delta": {"tool_calls": tool_call, "finish_reason": "tool_calls"}
                }]
            }
        ))
    ):
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            with patch("src.router.core.asyncio.sleep", new=AsyncMock()):
                client = TestClient(app)
                resp = client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4", "messages": [{"role": "user", "content": "Tool"}], "stream": True},
                )
                assert resp.status_code == 200
                lines = [l.strip() for l in resp.iter_lines() if l.strip().startswith("data:")]
                tool_line = next((l for l in lines if "tool_calls" in l), None)
                assert tool_line is not None
                tool_delta = json.loads(tool_line.split("data: ", 1)[1])
                assert "delta" in tool_delta
                assert "tool_calls" in tool_delta["delta"]
                assert tool_delta["delta"]["tool_calls"][0]["index"] == 0
                assert tool_delta["delta"]["finish_reason"] == "tool_calls"
                done_line = next((l for l in lines if "[DONE]" in l), None)
                assert done_line is not None


def test_chat_completions_streaming_error_then_done():
    async def faulty_route(*args, **kwargs):
        raise RuntimeError("Network boom")
    with patch.object(
        router_core, "route_request", side_effect=faulty_route
    ):
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            with patch("src.router.core.asyncio.sleep", new=AsyncMock()):
                client = TestClient(app)
                resp = client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4", "messages": [{"role": "user", "content": "boom"}], "stream": True},
                )
                assert resp.status_code == 200
                lines = [l.strip() for l in resp.iter_lines() if l.strip().startswith("data:")]
                error_chunks = [l for l in lines if "error" in l or "api_error" in l]
                assert any(b"server_error" in l or b"api_error" in l for l in error_chunks)
                done_line = next((l for l in lines if "[DONE]" in l), None)
                assert done_line is not None


# ---------------------------------------------------------------------
# 3. POST /v1/completions
# ---------------------------------------------------------------------
def test_completions_non_streaming():
    with patch.object(
        router_core, "route_request", new=AsyncMock(return_value=_make_routing_response(
            usage={"prompt_tokens": 5, "completion_tokens": 9}
        ))
    ):
        client = TestClient(app)
        resp = client.post(
            "/v1/completions",
            json={"model": "gpt-3.5-turbo", "prompt": "Hello\nWorld"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "text_completion"
        # Router returns a header indicating the default max_tokens used
        assert resp.headers.get("x-rr-max-tokens") == "16"


def test_completions_streaming_TextAndDone():
    with patch.object(
        router_core, "route_request", new=AsyncMock(return_value=_make_routing_response(
            usage={"prompt_tokens": 3, "completion_tokens": 5}
        ))
    ):
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            with patch("src.router.core.asyncio.sleep", new=AsyncMock()):
                client = TestClient(app)
                resp = client.post("/v1/completions", json={"model": "gpt-3.5-turbo", "stream": True})
                assert resp.status_code == 200
                assert resp.headers.get("content-type", "").startswith("text/event-stream")
                lines = [l.strip() for l in resp.iter_lines() if l.strip().startswith("data:")]
                chunk_line = next((l for l in lines if "delta.text" in l), None)
                assert chunk_line is not None
                delta_json = json.loads(chunk_line.split("data: ", 1)[1])
                assert delta_json["delta"]["text"] == "Chunk"
                done_line = next((l for l in lines if "[DONE]" in l), None)
                assert done_line is not None


def test_completions_streaming_ErrorThenDone():
    async def error_route(*args, **kwargs):
        raise RuntimeError("boom")
    with patch.object(
        router_core, "route_request", side_effect=error_route
    ):
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            with patch("src.router.core.asyncio.sleep", new=AsyncMock()):
                client = TestClient(app)
                resp = client.post("/v1/completions", json={"model": "gpt-3.5-turbo", "stream": True})
                assert resp.status_code == 200
                lines = [l.strip() for l in resp.iter_lines() if l.strip().startswith("data:")]
                error_chunks = [l for l in lines if "error" in l or "api_error" in l]
                assert any(b"server_error" in l or b"api_error" in l for l in error_chunks)
                done_line = next((l for l in lines if "[DONE]" in l), None)
                assert done_line is not None


# ---------------------------------------------------------------------
# 4. GET /.well-known/agent-card.json
# ---------------------------------------------------------------------
def test_agent_card_success():
    patch_models = patch.object(router_core, "models", {
        "model-1": {"max_input_tokens": 32768},
        "model-2": {"max_input_tokens": 4096}
    })
    patch_lb = patch.object(router_core, "load_balancer", return_value=None)
    patch_cm = patch.object(capability_manager, "get_capabilities",
                            return_value={"supports_tools": True})
    with patch_models, patch_lb, patch_cm:
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            client = TestClient(app)
            resp = client.get("/.well-known/agent-card.json")
            assert resp.status_code == 200
            card = resp.json()
            assert card["name"] == "RealityRouter"
            assert "version" in card
            assert card["max_input_tokens"] == 32768
            assert "capabilities" in card
            assert "supported_features" in card["capabilities"]
            assert "tools" in card["capabilities"]["supported_features"]
            assert card["capabilities"]["supported_features"]["tools"] is True


# ---------------------------------------------------------------------
# 5. POST /metrics/log
# ---------------------------------------------------------------------
def get_db():
    # The real app provides this via dependency injection; we will override it in the test.
    raise RuntimeError("Dependency not overridden in this test")

def test_metrics_log_success():
    metric_entry = {
        "timestamp": "2026-11-23T04:45:00+00:00",
        "model_id": "gpt-3.5-turbo",
        "cost": 0.001,
        "time": 0.123,
        "probability": 0.95,
        "success": True,
        "query": "SELECT * FROM users",
    }
    # Create temp SQLite DB
    temp_fd, temp_path = tempfile.mkstemp(suffix=".db")
    os.close(temp_fd)
    engine = create_engine(f"sqlite:///{temp_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    app.dependency_overrides[get_db] = lambda: engine
    try:
        client = TestClient(app)
        resp = client.post("/metrics/log", json=metric_entry)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert isinstance(data["id"], int)
        # Verify a row was persisted
        with engine.connect() as conn:
            result = conn.execute("SELECT COUNT(*) FROM routing_log")
            assert result.scalar() == 1
    finally:
        app.dependency_overrides.clear()
        os.unlink(temp_path)


def test_metrics_log_malformed_timestamp():
    bad_entry = {
        "timestamp": "not-a-timestamp",
        "model_id": "gpt-3.5-turbo",
        "cost": 0.001,
        "time": 0.123,
        "probability": 0.95,
        "success": True,
        "query": "SELECT * FROM users",
    }
    temp_fd, temp_path = tempfile.mkstemp(suffix=".db")
    os.close(temp_fd)
    engine = create_engine(f"sqlite:///{temp_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_db] = lambda: engine
    try:
        client = TestClient(app)
        resp = client.post("/metrics/log", json=bad_entry)
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in str(data).lower() or "status" in data.lower()
    finally:
        app.dependency_overrides.clear()
        os.unlink(temp_path)

