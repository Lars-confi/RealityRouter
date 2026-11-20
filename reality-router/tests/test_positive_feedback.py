"""Fast, deterministic simulated-LLM and simulated-feedback regression tests.

Covers the full positive-feedback pipeline without calling live external LLMs:
1. GUI metrics aggregation (`GET /metrics/summary` and `GET /metrics/dashboard`)
2. Sentiment label parsing & adapter parameters (`_parse_sentiment_label`, `assess_user_sentiment`)
3. Multi-turn simulated user follow-up feedback (both with and without `reality_check_id`)
4. Simulated tool-agent observation success followed by a normal human `"happy"` follow-up
   (verifying stale tool observations do not hijack later human turns)
5. Malformed tool-call validation ordering (no bogus `feedback: 1` before JSON validation)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.config.settings import get_settings
from src.main import app
from src.models.database import RoutingLog, SessionLocal
from src.models.routing import RoutingRequest
from src.router.agent_detector import classify_observation
from src.router.core import RouterCore, router_core
from tests.conftest import MockHTTPXResponse

client = TestClient(app)

RUN_TESTS_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run unit tests",
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
        },
    }
]


def _delete_logs_for_model(model_id: str) -> None:
    db = SessionLocal()
    try:
        db.query(RoutingLog).filter(RoutingLog.model_id == model_id).delete()
        db.commit()
    finally:
        db.close()


def _snapshot_router_state():
    settings = get_settings()
    return {
        "models": dict(router_core.models),
        "adapters": dict(router_core.adapters),
        "is_model_healthy": router_core.load_balancer.is_model_healthy,
        "reality_routing_url": settings.reality_routing_url,
        "reality_rerouting_url": settings.reality_rerouting_url,
        "reality_check_token": settings.reality_check_token,
        "reality_check_provider": settings.reality_check_provider,
        "default_strategy": settings.default_strategy,
        "sentiment_model_id": settings.sentiment_model_id,
        "enable_sticky_sessions": settings.enable_sticky_sessions,
    }


def _restore_router_state(snap):
    settings = get_settings()
    router_core.models = snap["models"]
    router_core.adapters = snap["adapters"]
    router_core.load_balancer.is_model_healthy = snap["is_model_healthy"]
    settings.reality_routing_url = snap["reality_routing_url"]
    settings.reality_rerouting_url = snap["reality_rerouting_url"]
    settings.reality_check_token = snap["reality_check_token"]
    settings.reality_check_provider = snap["reality_check_provider"]
    settings.default_strategy = snap["default_strategy"]
    settings.sentiment_model_id = snap["sentiment_model_id"]
    settings.enable_sticky_sessions = snap["enable_sticky_sessions"]


def test_gui_metrics_summary_and_dashboard_positive_feedback_aggregation():
    """Positive/negative/indeterminate sentiment labels aggregate accurately for GUI."""
    test_model = "sim-feedback-metrics-model"
    _delete_logs_for_model(test_model)
    sentiments = [
        "happy",
        "observation_success",
        "observation_progress",
        "unhappy",
        "observation_failure",
        "observation_stagnation",
        "indeterminate",
    ]
    db = SessionLocal()
    try:
        for idx, sentiment in enumerate(sentiments):
            db.add(
                RoutingLog(
                    query=f"query-{idx}",
                    agent_id="sim-metrics-agent",
                    model_id=test_model,
                    model_name="Simulated Feedback Metrics Model",
                    expected_utility=0.8,
                    cost=0.001,
                    time=0.5,
                    probability=0.9,
                    success=True,
                    user_sentiment=sentiment,
                    strategy="expected_utility",
                )
            )
        db.commit()

        summary_resp = client.get("/metrics/summary?window=all")
        assert summary_resp.status_code == 200
        summary = summary_resp.json()
        assert test_model in summary["models"]
        stats = summary["models"][test_model]
        assert stats["positive_feedback_count"] == 3
        assert stats["feedback_count"] == 6

        dashboard_resp = client.get("/metrics/dashboard")
        assert dashboard_resp.status_code == 200
        assert "positive_feedback_count" in dashboard_resp.text
    finally:
        db.close()
        _delete_logs_for_model(test_model)


def test_parse_sentiment_label_and_assess_user_sentiment_params():
    """Parser handles think tags, verbose wrappers, negation, and reasoning fallback."""
    assert RouterCore._parse_sentiment_label("happy") == "happy"
    assert (
        RouterCore._parse_sentiment_label(
            "<think>Deciding if user is happy or unhappy.</think>\nhappy"
        )
        == "happy"
    )
    assert RouterCore._parse_sentiment_label("Sentiment: happy.") == "happy"
    assert RouterCore._parse_sentiment_label("The user is not unhappy") == "happy"
    assert (
        RouterCore._parse_sentiment_label(
            "Let me check whether the user is happy or unhappy in this thread...",
            reasoning_content="Let me check whether the user is happy or unhappy in this thread...",
        )
        == "indeterminate"
    )

    core = RouterCore()
    core.models = {
        "sim-sentiment-model": {
            "name": "Simulated Sentiment Model",
            "cost": 0.0001,
            "time": 0.2,
            "probability": 0.9,
        }
    }
    captured_params = {}

    async def fake_forward(req):
        captured_params.update(req.parameters or {})
        return {"text": "<think>happy or unhappy?</think>\nhappy"}

    adapter = AsyncMock()
    adapter.forward_request.side_effect = fake_forward
    core.adapters = {"sim-sentiment-model": adapter}

    settings = get_settings()
    orig_sentiment_model = settings.sentiment_model_id
    settings.sentiment_model_id = "sim-sentiment-model"
    try:
        req = RoutingRequest(
            query="Thanks, that worked!",
            agent_id="sim-sentiment-agent",
            parameters={
                "messages": [
                    {"role": "user", "content": "Fix this bug"},
                    {"role": "assistant", "content": "Fixed it."},
                    {"role": "user", "content": "Thanks, that worked!"},
                ]
            },
        )
        result = asyncio.run(core.assess_user_sentiment(req))
        assert result == "happy"
        assert "temperature" not in captured_params
    finally:
        settings.sentiment_model_id = orig_sentiment_model


@pytest.mark.parametrize("with_reality_check_id", [True, False])
def test_simulated_multi_turn_user_followup_records_positive_feedback(with_reality_check_id):
    """Multi-turn simulated chat records 'happy' in DB/GUI with and without reality_check_id."""
    snap = _snapshot_router_state()
    model_id = (
        "sim-multiturn-rc-model"
        if with_reality_check_id
        else "sim-multiturn-norc-model"
    )
    agent_id = (
        "sim-multiturn-rc-agent"
        if with_reality_check_id
        else "sim-multiturn-norc-agent"
    )
    sentiment_model_id = "sim-multiturn-sentiment"
    _delete_logs_for_model(model_id)

    try:
        settings = get_settings()
        settings.reality_routing_url = "http://127.0.0.1:18081"
        settings.reality_rerouting_url = "http://127.0.0.1:18082"
        settings.reality_check_token = "local_unauthenticated"
        settings.reality_check_provider = "Enterprise"
        settings.default_strategy = "expected_utility"
        settings.sentiment_model_id = sentiment_model_id
        settings.enable_sticky_sessions = False

        router_core.models = {
            model_id: {
                "name": "Simulated Multi-Turn Model",
                "cost": 0.001,
                "time": 0.5,
                "probability": 0.85,
                "supports_function_calling": True,
            }
        }
        chat_adapter = AsyncMock()
        chat_adapter.forward_request.side_effect = [
            {
                "text": "Here is the fixed snippet.",
                "usage": {"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22},
            },
            {
                "text": "Glad that helped!",
                "usage": {"prompt_tokens": 14, "completion_tokens": 6, "total_tokens": 20},
            },
        ]
        sentiment_adapter = AsyncMock()
        sentiment_adapter.forward_request.return_value = {
            "text": "<think>checking happy or unhappy</think>\nhappy"
        }
        router_core.adapters = {
            model_id: chat_adapter,
            sentiment_model_id: sentiment_adapter,
        }
        router_core.load_balancer.is_model_healthy = MagicMock(return_value=True)

        feedback_posts = []

        async def mock_httpx_post(url, json=None, headers=None, timeout=None, **kwargs):
            if url.endswith("/decide"):
                if with_reality_check_id:
                    return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 501})
                return MockHTTPXResponse({"prob_true": 0.9})
            if url.endswith("/feedback"):
                feedback_posts.append((url, json))
                return MockHTTPXResponse({"status": "ok"})
            return MockHTTPXResponse({})

        with patch("httpx.AsyncClient.post", side_effect=mock_httpx_post):
            r1 = client.post(
                "/v1/chat/completions",
                json={
                    "model": model_id,
                    "agent_id": agent_id,
                    "messages": [{"role": "user", "content": "Help me sort a list"}],
                },
            )
            assert r1.status_code == 200

            r2 = client.post(
                "/v1/chat/completions",
                json={
                    "model": model_id,
                    "agent_id": agent_id,
                    "messages": [
                        {"role": "user", "content": "Help me sort a list"},
                        {"role": "assistant", "content": "Here is the fixed snippet."},
                        {"role": "user", "content": "Thanks, that worked!"},
                    ],
                },
            )
            assert r2.status_code == 200

        db = SessionLocal()
        try:
            logs = (
                db.query(RoutingLog)
                .filter(RoutingLog.model_id == model_id)
                .order_by(RoutingLog.id.asc())
                .all()
            )
            assert len(logs) == 2
            assert logs[0].user_sentiment == "happy"
        finally:
            db.close()

        if with_reality_check_id:
            assert any(
                url == "http://127.0.0.1:18081/feedback"
                and payload == {"decision_id": 501, "feedback": 1}
                for url, payload in feedback_posts
            )
        else:
            assert feedback_posts == []

        summary_resp = client.get("/metrics/summary?window=all")
        assert summary_resp.status_code == 200
        stats = summary_resp.json()["models"][model_id]
        assert stats["positive_feedback_count"] == 1
        assert stats["feedback_count"] == 1
    finally:
        _restore_router_state(snap)
        _delete_logs_for_model(model_id)


def test_tool_observation_then_human_followup_no_stale_hijack():
    """Tool observation logs positive feedback without hijacking later human follow-up."""
    snap = _snapshot_router_state()
    model_id = "sim-tool-followup-model"
    agent_id = "sim-tool-followup-agent"
    sentiment_model_id = "sim-tool-sentiment"
    _delete_logs_for_model(model_id)

    try:
        settings = get_settings()
        settings.reality_routing_url = "http://127.0.0.1:18081"
        settings.reality_rerouting_url = "http://127.0.0.1:18082"
        settings.reality_check_token = "local_unauthenticated"
        settings.reality_check_provider = "Enterprise"
        settings.default_strategy = "expected_utility"
        settings.sentiment_model_id = sentiment_model_id
        settings.enable_sticky_sessions = False

        router_core.models = {
            model_id: {
                "name": "Simulated Tool Followup Model",
                "cost": 0.001,
                "time": 0.5,
                "probability": 0.9,
                "supports_function_calling": True,
            }
        }
        chat_adapter = AsyncMock()
        chat_adapter.forward_request.side_effect = [
            {
                "text": "",
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call_ok_1",
                        "type": "function",
                        "function": {"name": "run_tests", "arguments": '{"target": "unit"}'},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            },
            {
                "text": "All tests passed!",
                "usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26},
            },
            {
                "text": "You are welcome!",
                "usage": {"prompt_tokens": 25, "completion_tokens": 5, "total_tokens": 30},
            },
        ]
        sentiment_adapter = AsyncMock()
        sentiment_adapter.forward_request.return_value = {"text": "happy"}
        router_core.adapters = {
            model_id: chat_adapter,
            sentiment_model_id: sentiment_adapter,
        }
        router_core.load_balancer.is_model_healthy = MagicMock(return_value=True)

        decision_seq = iter([601, 602, 603])
        feedback_posts = []

        async def mock_httpx_post(url, json=None, headers=None, timeout=None, **kwargs):
            if url.endswith("/decide"):
                return MockHTTPXResponse(
                    {"prob_true": 0.9, "decision_id": next(decision_seq)}
                )
            if url.endswith("/feedback"):
                feedback_posts.append((url, json))
                return MockHTTPXResponse({"status": "ok"})
            return MockHTTPXResponse({})

        with patch("httpx.AsyncClient.post", side_effect=mock_httpx_post):
            # Turn 1: model emits a valid tool call
            t1 = client.post(
                "/v1/chat/completions",
                json={
                    "model": model_id,
                    "agent_id": agent_id,
                    "tools": RUN_TESTS_TOOL,
                    "messages": [{"role": "user", "content": "Run the unit tests"}],
                },
            )
            assert t1.status_code == 200

            # Turn 2: client submits successful tool observation
            turn2_messages = [
                {"role": "user", "content": "Run the unit tests"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_ok_1",
                            "type": "function",
                            "function": {
                                "name": "run_tests",
                                "arguments": '{"target": "unit"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_ok_1",
                    "content": "Exit code: 0\nAll tests passed",
                },
            ]
            t2 = client.post(
                "/v1/chat/completions",
                json={
                    "model": model_id,
                    "agent_id": agent_id,
                    "tools": RUN_TESTS_TOOL,
                    "messages": turn2_messages,
                },
            )
            assert t2.status_code == 200

            mid_summary = client.get("/metrics/summary?window=all").json()["models"][model_id]
            assert mid_summary["positive_feedback_count"] == 1
            assert mid_summary["feedback_count"] == 1

            # Turn 3: normal human follow-up in the same conversation containing the earlier tool message
            turn3_messages = turn2_messages + [
                {"role": "assistant", "content": "All tests passed!"},
                {"role": "user", "content": "Great, thanks!"},
            ]
            assert classify_observation(turn3_messages) == "unknown"

            t3 = client.post(
                "/v1/chat/completions",
                json={
                    "model": model_id,
                    "agent_id": agent_id,
                    "tools": RUN_TESTS_TOOL,
                    "messages": turn3_messages,
                },
            )
            assert t3.status_code == 200

        db = SessionLocal()
        try:
            logs = (
                db.query(RoutingLog)
                .filter(RoutingLog.model_id == model_id)
                .order_by(RoutingLog.id.asc())
                .all()
            )
            assert len(logs) == 3
            assert logs[0].user_sentiment == "observation_success"
            assert logs[1].user_sentiment == "happy"
        finally:
            db.close()

        assert any(
            payload == {"decision_id": 601, "feedback": 1}
            for _, payload in feedback_posts
        )
        assert any(
            payload == {"decision_id": 602, "feedback": 1}
            for _, payload in feedback_posts
        )

        final_summary = client.get("/metrics/summary?window=all").json()["models"][model_id]
        assert final_summary["positive_feedback_count"] == 2
        assert final_summary["feedback_count"] == 2
    finally:
        _restore_router_state(snap)
        _delete_logs_for_model(model_id)


def test_malformed_tool_call_does_not_post_positive_feedback():
    """Malformed tool-call JSON must not trigger auto-positive feedback: 1."""
    snap = _snapshot_router_state()
    model_id = "sim-malformed-tool-model"
    agent_id = "sim-malformed-tool-agent"
    _delete_logs_for_model(model_id)

    try:
        settings = get_settings()
        settings.reality_routing_url = "http://127.0.0.1:18081"
        settings.reality_rerouting_url = "http://127.0.0.1:18082"
        settings.reality_check_token = "local_unauthenticated"
        settings.reality_check_provider = "Enterprise"
        settings.default_strategy = "expected_utility"
        settings.enable_sticky_sessions = False

        router_core.models = {
            model_id: {
                "name": "Simulated Malformed Tool Model",
                "cost": 0.001,
                "time": 0.5,
                "probability": 0.8,
                "supports_function_calling": True,
            }
        }
        bad_tool_adapter = AsyncMock()
        bad_tool_adapter.forward_request.return_value = {
            "text": "",
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_bad_1",
                    "type": "function",
                    "function": {"name": "run_tests", "arguments": "{bad json"},
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13},
        }
        router_core.adapters = {model_id: bad_tool_adapter}
        router_core.load_balancer.is_model_healthy = MagicMock(return_value=True)

        feedback_posts = []

        async def mock_httpx_post(url, json=None, headers=None, timeout=None, **kwargs):
            if url.endswith("/decide"):
                return MockHTTPXResponse({"prob_true": 0.85, "decision_id": 701})
            if url.endswith("/feedback"):
                feedback_posts.append((url, json))
                return MockHTTPXResponse({"status": "ok"})
            return MockHTTPXResponse({})

        with patch("httpx.AsyncClient.post", side_effect=mock_httpx_post):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": model_id,
                    "agent_id": agent_id,
                    "tools": RUN_TESTS_TOOL,
                    "messages": [{"role": "user", "content": "Call the tool"}],
                },
            )
            assert resp.status_code in (200, 500, 502, 503)

        assert not any(
            payload == {"decision_id": 701, "feedback": 1}
            for _, payload in feedback_posts
        )
        assert any(
            payload.get("decision_id") == 701 and payload.get("feedback") == 0
            for _, payload in feedback_posts
        )
    finally:
        _restore_router_state(snap)
        _delete_logs_for_model(model_id)
