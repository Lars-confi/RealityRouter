"""Deterministic offline HTTP public API & SSE streaming contract tests."""
"""All network calls are mocked; no live external access."""
"""Only pure unit/contract verified against isolated test harness."""
"""Created per IMPLEMENT task rr-qa-01c."""
import json
import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from src.models.routing import RoutingResponse
from src.router.core import router_core
from src.utils.capability_tester import capability_manager

# ---------------------------------------------------------------------
# Helper: mock RoutingResponse with required fields
# ---------------------------------------------------------------------
def _mock_routing_response(
    *,
    response_text="OK",
    usage=None,
    tool_calls=None,
    finish_reason="stop",
    model_id="mock-model-id",
    model_name="mock-model-name",
    extra_json=None,
    status_code=200,
):
    """
    Create an AsyncMock that mimics a RoutingResponse.json() call.
    Matches the pydantic model structure used by the router.
    """
    resp_dict = {
        "agent_id": "default",
        "model_id": model_id,
        "model_name": model_name,
        "expected_utility": 1.0,
        "cost": 0.0,
        "time": 0.0,
        "probability": 1.0,
        "timestamp": None,
        "response": {"text": response_text}
    }
    if usage:
        resp_dict["response"]["usage"] = usage.copy()
    if tool_calls:
        resp_dict["response"]["tool_calls"] = tool_calls
    if finish_reason:
        resp_dict["response"]["finish_reason"] = finish_reason
    if extra_json:
        resp_dict["response"].update(extra_json)

    mock_resp = AsyncMock()
    mock_resp.headers = {}
    mock_resp.json.return_value = resp_dict
    return mock_resp


# ---------------------------------------------------------------------
# 1. POST /v1/messages (Anthropic Messages API)
# ---------------------------------------------------------------------
def test_messages_non_streaming_text():
    """Non‑streaming text response – verify status, Anthropic shape, and custom headers."""
    with patch.object(router_core, "route_request", new=AsyncMock(return_value=_mock_routing_response(
        extra_json={
            "content": {"type": "text", "text": "Hello World"},
            "usage": {"prompt_tokens": 5, "completion_tokens": 9}
        }
    ))):
        client = TestClient(app)
        resp = client.post(
            "/v1/messages",
            json={
                "role": "user",
                "content": "Say hello",
                "stream": False
            }
        )
        assert resp.status_code == 200
        # Verify custom routing headers
        assert resp.headers.get("x-rr-model") == "mock-model-id"
        assert resp.headers.get("x-rr-strategy") == "mock-strategy"
        assert resp.headers.get("x-rr-cost") == "0.0"
        assert resp.headers.get("x-rr-saved") == "true"
        data = resp.json()
        # Content block list should exist
        assert isinstance(data["content"], list)
        # Expect at least one text block
        assert any(isinstance(b, dict) and b.get("type") == "text" for b in data["content"])
        assert data["content"][0]["text"] == "Hello World"
        # finish_reason should indicate successful completion
        assert data["stop_reason"] in ("stop", "tool_use")


def test_messages_tools_roundtrip():
    """tools + tool_use/tool_result round‑trip – verify Anthropic tool_use block."""
    # Build the tool_call payload that flows through the routing logic
    tool_block = [{
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "my_tool",
            "arguments": '{"foo": "bar"}'
        }
    }]
    with patch.object(router_core, "route_request", new=AsyncMock(return_value=_mock_routing_response(
        extra_json={
            "content": [{
                "type": "tool_use",
                "tool_name": "my_tool",
                "input_schema": {"type": "object", "properties": {"foo": {"type": "string"}}},
                "tool_input": {"foo": "bar"},
                "stop_reason": "tool_use"
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }
    ))):
        client = TestClient(app)
        resp = client.post(
            "/v1/messages",
            json={
                "role": "user",
                "content": "Do a thing",
                "stream": False,
                "tools": [{"name": "my_tool", "description": "d", "input_schema": {"type": "object", "properties": {"foo": {"type": "string"}}}}]
            }
        )
        assert resp.status_code == 200
        data = resp.json()
        # Extract tool_use blocks
        tool_blocks = [c for c in data.get("content", []) if c.get("type") == "tool_use"]
        assert len(tool_blocks) == 1
        tb = tool_blocks[0]
        assert tb["tool_name"] == "my_tool"
        assert tb["stop_reason"] == "tool_use"


def test_messages_streaming_sse():
    """Streaming SSE – verify SSE framing and event flow."""
    streaming_payload = {
        "content": [
            {"type": "text", "text": "Chunk 1"},
            {"type": "text", "text": "Chunk 2"}
        ],
        "finish_reason": "stop"
    }
    # Build exact SSE frames the endpoint should yield
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

    with patch.object(router_core, "route_request", new=AsyncMock(return_value=_mock_routing_response(
        extra_json={"content": streaming_payload}
    ))):
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            with patch("src.router.core.asyncio.sleep", new=AsyncMock()):
                client = TestClient(app)
                resp = client.post(
                    "/v1/messages?stream=true", json={
                        "role": "user", "content": "Stream me"
                    }, params={"stream": True}
                )
                assert resp.status_code == 200
                # FastAPI uses "content-type" for SSE, not "media-type"
                assert resp.headers.get("content-type", "").startswith("text/event-stream")
                lines = [l.strip() for l in resp.iter_lines() if l.startswith(b"data:")]
                payloads = [json.loads(l.decode().removeprefix("data: ")) for l in lines]
                # Must contain delta messages and a stop event
                assert len(payloads) >= 3
                assert any(p.get("event") == "message_stop" for p in payloads)


# ---------------------------------------------------------------------
# 2. POST /v1/chat/completions (stream=True)
# ---------------------------------------------------------------------
def test_chat_completions_streaming_text():
    """Text + usage chunk – verify OpenAI SSE format and final DONE."""
    with patch.object(router_core, "route_request", new=AsyncMock(return_value=_mock_routing_response(
        usage={"prompt_tokens": 5, "completion_tokens": 7}
    ))):
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            with patch("src.router.core.asyncio.sleep", new=AsyncMock()):
                client = TestClient(app)
                resp = client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "Hello"}], "stream": True}
                )
                assert resp.status_code == 200
                assert resp.headers.get("content-type", "").startswith("text/event-stream")
                lines = [l.strip() for l in resp.iter_lines() if l.startswith(b"data:")]
                chunk_line = next((l for l in lines if b"object: chat.completion.chunk" in l), None)
                assert chunk_line is not None
                delta_json = json.loads(chunk_line.lstrip(b"data: "))
                assert isinstance(delta_json, dict)
                assert "delta" in delta_json
                assert "content" in delta_json["delta"]
                assert "usage" in delta_json["delta"]
                done_line = next((l for l in lines if b"[DONE]" in l), None)
                assert done_line is not None


def test_chat_completions_streaming_tool_calls():
    """Tool‑call chunk – verify indexed tool_calls and finish_reason."""
    tool_call = [{
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "my_tool", "arguments": '{"x": 1}'}
    }]
    with patch.object(router_core, "route_request", new=AsyncMock(return_value=_mock_routing_response(
        json_data={
            "choices": [{
                "delta": {"tool_calls": tool_call, "finish_reason": "tool_calls"}
            }]
        }
    ))):
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            with patch("src.router.core.asyncio.sleep", new=AsyncMock()):
                client = TestClient(app)
                resp = client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4", "messages": [{"role": "user", "content": "Tool"}], "stream": True}
                )
                assert resp.status_code == 200
                lines = [l.strip() for l in resp.iter_lines() if l.startswith(b"data:")]
                tool_line = next((l for l in lines if b"tool_calls" in l), None)
                assert tool_line is not None
                tool_delta = json.loads(tool_line.lstrip(b"data: "))
                assert "delta" in tool_delta
                assert "tool_calls" in tool_delta["delta"]
                assert tool_delta["delta"]["tool_calls"][0]["index"] == 0
                assert tool_delta["delta"]["finish_reason"] == "tool_calls"
                done_line = next((l for l in lines if b"[DONE]" in l), None)
                assert done_line is not None


def test_chat_completions_streaming_error_then_done():
    """Error during streaming – should emit error JSON then DONE."""
    async def faulty_route(*args, **kwargs):
        raise RuntimeError("Network boom")
    with patch.object(router_core, "route_request", side_effect=faulty_route):
        # Still need asyncio & sleep patches
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            with patch("src.router.core.asyncio.sleep", new=AsyncMock()):
                client = TestClient(app)
                resp = client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4", "messages": [{"role": "user", "content": "boom"}], "stream": True}
                )
                assert resp.status_code == 200
                lines = [l.strip() for l in resp.iter_lines() if l.startswith(b"data:")]
                error_chunks = [l for l in lines if b"error" in l]
                assert any(b"server_error" in l or b"api_error" in l for l in error_chunks)
                done_line = next((l for l in lines if b"[DONE]" in l), None)
                assert done_line is not None


# ---------------------------------------------------------------------
# 3. POST /v1/completions (non-streaming + streaming)
# ---------------------------------------------------------------------
def test_completions_non_streaming():
    """Non‑streaming completion – verify response shape and default max_tokens."""
    with patch.object(router_core, "route_request", new=AsyncMock(return_value=_mock_routing_response(
        usage={"prompt_tokens": 5, "completion_tokens": 9}
    ))):
        client = TestClient(app)
        resp = client.post("/v1/completions", json={"model": "gpt-3.5-turbo", "prompt": "Hello\nWorld"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "text_completion"
        # Router returns a header indicating the default max_tokens it used
        assert resp.headers.get("x-rr-max-tokens") == "16"


def test_completions_streaming_TextAndDone():
    """Streaming completion – verify text delta chunk and DONE marker."""
    with patch.object(router_core, "route_request", new=AsyncMock(return_value=_mock_routing_response(
        usage={"prompt_tokens": 3, "completion_tokens": 5}
    ))):
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            with patch("src.router.core.asyncio.sleep", new=AsyncMock()):
                client = TestClient(app)
                resp = client.post("/v1/completions", json={"model": "gpt-3.5-turbo", "stream": True})
                assert resp.status_code == 200
                assert resp.headers.get("content-type", "").startswith("text/event-stream")
                lines = [l.strip() for l in resp.iter_lines() if l.startswith(b"data:")]
                # Locate a delta.text chunk
                chunk_line = next((l for l in lines if b"delta.text" in l), None)
                assert chunk_line is not None
                delta_json = json.loads(chunk_line.lstrip(b"data: "))
                assert delta_json["delta"]["text"] == "Chunk"
                done_line = next((l for l in lines if b"[DONE]" in l), None)
                assert done_line is not None


def test_completions_streaming_ErrorThenDone():
    """Streaming error – emit error JSON then DONE marker."""
    async def error_route(*args, **kwargs):
        raise RuntimeError("boom")
    with patch.object(router_core, "route_request", side_effect=error_route):
        with patch.object(router_core, "asyncio", new=AsyncMock()):
            with patch("src.router.core.asyncio.sleep", new=AsyncMock()):
                client = TestClient(app)
                resp = client.post("/v1/completions", json={"model": "gpt-3.5-turbo", "stream": True})
                assert resp.status_code == 200
                lines = [l.strip() for l in resp.iter_lines() if l.startswith(b"data:")]
                error_chunks = [l for l in lines if b"error" in l]
                assert any(b"server_error" in l for l in error_chunks)
                done_line = next((l for l in lines if b"[DONE]" in l), None)
                assert done_line is not None


# ---------------------------------------------------------------------
# 4. GET /.well-known/agent-card.json
# ---------------------------------------------------------------------
def test_agent_card_success():
    """Validate agent-card JSON shape and derived fields."""
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
