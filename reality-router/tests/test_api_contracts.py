"""Deterministic offline HTTP public API and SSE streaming contract tests."""

import json
import os
import tempfile
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.main import app
from src.models.database import Base, RoutingLog, get_db
from src.models.routing import RoutingResponse
from src.router.core import router_core


def _mk_rsp(text="Hello", tool_calls=None, finish_reason="stop", usage=None, model_id="mock-model"):
    resp = {"text": text, "finish_reason": finish_reason}
    if tool_calls is not None:
        resp["tool_calls"] = tool_calls
    if usage is not None:
        resp["usage"] = dict(usage)
    return RoutingResponse(
        agent_id="default",
        model_id=model_id,
        model_name="MockModel",
        expected_utility=0.9,
        cost=0.001,
        time=0.1,
        probability=0.95,
        response=resp,
    )


def _sse_data_chunks(text: str):
    chunks = []
    done = False
    for line in text.splitlines():
        if line == "data: [DONE]":
            done = True
        elif line.startswith("data: "):
            chunks.append(json.loads(line[6:]))
    return chunks, done


def test_messages_non_streaming_text():
    rsp = _mk_rsp("Hello World", usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8})
    with patch.object(router_core, "route_request", new=AsyncMock(return_value=rsp)):
        resp = TestClient(app).post(
            "/v1/messages",
            json={"model": "auto", "max_tokens": 64, "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert resp.status_code == 200
    assert resp.headers["x-rr-model"] == "mock-model"
    for h in ("x-rr-strategy", "x-rr-cost", "x-rr-saved"):
        assert h in resp.headers
    data = resp.json()
    assert data["content"][0] == {"type": "text", "text": "Hello World"}
    assert data["stop_reason"] == "end_turn"


def test_messages_tools_roundtrip():
    tc = [{"id": "call_1", "type": "function", "function": {"name": "calc", "arguments": '{"x": 1}'}}]
    rsp = _mk_rsp("", tool_calls=tc, finish_reason="tool_calls")
    with patch.object(router_core, "route_request", new=AsyncMock(return_value=rsp)):
        resp = TestClient(app).post(
            "/v1/messages",
            json={
                "model": "auto",
                "max_tokens": 64,
                "system": "Be terse",
                "tools": [{"name": "calc", "description": "c", "input_schema": {"type": "object", "properties": {"x": {"type": "integer"}}}}],
                "messages": [
                    {"role": "user", "content": "calc 1"},
                    {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "calc", "input": {"x": 1}}]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "2"}]},
                ],
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["stop_reason"] == "tool_use"
    assert data["content"][0] == {"type": "tool_use", "id": "call_1", "name": "calc", "input": {"x": 1}}


def test_messages_streaming_sse_and_errors():
    client = TestClient(app)
    with patch.object(router_core, "route_request", new=AsyncMock(return_value=_mk_rsp("Streamed"))), \
         patch("src.router.core.asyncio.sleep", new=AsyncMock()):
        resp = client.post(
            "/v1/messages",
            json={"model": "auto", "max_tokens": 64, "stream": True, "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = [l for l in resp.text.splitlines() if l.startswith("event: ")]
    assert "event: message_start" in events
    assert "event: content_block_delta" in events
    assert "event: message_stop" in events

    bad = client.post("/v1/messages", content=b"{bad", headers={"content-type": "application/json"})
    assert bad.status_code == 400

    with patch.object(router_core, "route_request", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch("src.router.core.asyncio.sleep", new=AsyncMock()):
        err_sse = client.post(
            "/v1/messages",
            json={"model": "auto", "max_tokens": 64, "stream": True, "messages": [{"role": "user", "content": "Hi"}]},
        )
        err_json = client.post(
            "/v1/messages",
            json={"model": "auto", "max_tokens": 64, "stream": False, "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert err_sse.status_code == 200
    assert "event: error" in err_sse.text and "api_error" in err_sse.text
    assert err_json.status_code == 500


def test_chat_completions_streaming_text_and_usage():
    rsp = _mk_rsp("Hi chat", usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6})
    with patch.object(router_core, "route_request", new=AsyncMock(return_value=rsp)), \
         patch("src.router.core.asyncio.sleep", new=AsyncMock()):
        resp = TestClient(app).post(
            "/v1/chat/completions",
            json={"model": "auto", "stream": True, "messages": [{"role": "user", "content": "Hello"}]},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    chunks, done = _sse_data_chunks(resp.text)
    assert done is True
    assert chunks[0]["choices"][0]["delta"]["content"] == "Hi chat"
    assert any("usage" in c and c["usage"]["total_tokens"] == 6 for c in chunks)


def test_chat_completions_streaming_tool_calls_and_error():
    tc = [{"id": "call_1", "type": "function", "function": {"name": "fn", "arguments": '{"k": 1}'}}]
    rsp = _mk_rsp("", tool_calls=tc, finish_reason="tool_calls")
    client = TestClient(app)
    with patch.object(router_core, "route_request", new=AsyncMock(return_value=rsp)), \
         patch("src.router.core.asyncio.sleep", new=AsyncMock()):
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "auto", "stream": True, "messages": [{"role": "user", "content": "Call fn"}]},
        )
    assert resp.status_code == 200
    chunks, done = _sse_data_chunks(resp.text)
    assert done is True
    choice = chunks[0]["choices"][0]
    assert choice["delta"]["tool_calls"][0]["index"] == 0
    assert choice["finish_reason"] == "tool_calls"

    with patch.object(router_core, "route_request", new=AsyncMock(side_effect=RuntimeError("down"))), \
         patch("src.router.core.asyncio.sleep", new=AsyncMock()):
        err = client.post(
            "/v1/chat/completions",
            json={"model": "auto", "stream": True, "messages": [{"role": "user", "content": "Boom"}]},
        )
    err_chunks, err_done = _sse_data_chunks(err.text)
    assert err_done is True
    assert err_chunks[0]["error"]["type"] == "server_error"


def test_completions_non_streaming_and_streaming():
    client = TestClient(app)
    with patch.object(router_core, "route_request", new=AsyncMock(return_value=_mk_rsp("Done"))) as mock_route:
        r_def = client.post("/v1/completions", json={"model": "auto", "prompt": "Hi"})
        assert r_def.status_code == 200
        assert r_def.json()["choices"][0]["text"] == "Done"
        assert mock_route.call_args.args[0].parameters["max_tokens"] == 16

        r_ovr = client.post("/v1/completions", json={"model": "auto", "prompt": ["a", "b"], "max_tokens": 42})
        assert r_ovr.status_code == 200
        assert mock_route.call_args.args[0].query == "a\nb"
        assert mock_route.call_args.args[0].parameters["max_tokens"] == 42

    with patch.object(router_core, "route_request", new=AsyncMock(return_value=_mk_rsp("Chunk"))), \
         patch("src.router.core.asyncio.sleep", new=AsyncMock()):
        r_sse = client.post("/v1/completions", json={"model": "auto", "prompt": "Go", "stream": True})
    assert r_sse.status_code == 200
    assert r_sse.headers["content-type"].startswith("text/event-stream")
    chunks, done = _sse_data_chunks(r_sse.text)
    assert done is True and chunks[0]["choices"][0]["text"] == "Chunk"

    with patch.object(router_core, "route_request", new=AsyncMock(side_effect=RuntimeError("fail"))), \
         patch("src.router.core.asyncio.sleep", new=AsyncMock()):
        r_err = client.post("/v1/completions", json={"model": "auto", "prompt": "Go", "stream": True})
    err_chunks, err_done = _sse_data_chunks(r_err.text)
    assert err_done is True and err_chunks[0]["error"]["type"] == "server_error"


def test_agent_card_contract():
    with patch("src.router.core.load_balancer.get_models", return_value=["m1", "m2"]), \
         patch("src.router.core.capability_manager.get_capabilities", return_value={"supports_tools": True}), \
         patch.dict(router_core.models, {"m1": {"max_input_tokens": 32768}, "m2": {"max_input_tokens": 8192}}, clear=False):
        resp = TestClient(app).get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "RealityRouter"
    assert card["max_input_tokens"] == 32768
    assert card["capabilities"]["supported_features"]["tools"] is True


def test_metrics_log_persist_and_malformed_timestamp():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)
        entry = {
            "timestamp": "2026-11-23T12:00:00+00:00",
            "model_id": "mock-model",
            "cost": 0.001,
            "time": 0.12,
            "probability": 0.95,
            "success": True,
            "query": "hello",
        }
        ok = client.post("/metrics/log", json=entry)
        assert ok.status_code == 200
        body = ok.json()
        assert body["status"] == "success" and isinstance(body["id"], int)
        with Session() as db:
            row = db.query(RoutingLog).filter(RoutingLog.id == body["id"]).one()
            assert row.model_id == "mock-model"
            assert row.model_name == "mock-model"
            assert row.expected_utility == 0.0

        bad = client.post("/metrics/log", json={**entry, "timestamp": "not-a-timestamp"})
        assert bad.status_code == 200
        assert bad.json()["status"] == "error"
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        if os.path.exists(db_path):
            os.unlink(db_path)
