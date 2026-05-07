import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from src.models.routing import RoutingRequest
from src.router.core import RouterCore, RoutingDecision


@pytest.fixture
def mock_router():
    with (
        patch("src.router.core.SessionLocal") as mock_session,
        patch("src.router.core.ExpectedUtilityCalculator") as mock_calc,
        patch("src.router.core.load_balancer"),
        patch("src.router.core.get_settings"),
    ):
        # Make the mocked DB return empty list for recent logs
        mock_db = mock_session.return_value
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        # Because we might chain filter twice or once depending on logic
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = []
        mock_query.first.return_value = None
        mock_db.query.return_value = mock_query

        router = RouterCore()

        # Setup mock models
        router.models = {
            "model-tool-supporter": {
                "name": "Tool Supporter",
                "cost": 0.01,
                "time": 1.0,
                "probability": 0.9,
                "supports_function_calling": True,
            },
            "model-no-tools": {
                "name": "No Tools",
                "cost": 0.01,
                "time": 1.0,
                "probability": 0.9,
                "supports_function_calling": False,
            },
            "claude-3-5-sonnet": {
                "name": "Claude 3.5 Sonnet",
                "cost": 0.05,
                "time": 2.0,
                "probability": 0.9,
                "supports_function_calling": True,
            },
        }
        router.utility_calculator.calculate_expected_utility = MagicMock(
            return_value=10.0
        )
        yield router


@pytest.mark.asyncio
async def test_tool_request_detection_and_filtering(mock_router):
    request = RoutingRequest(
        query="Test query",
        agent_id="test_agent",
        parameters={"tools": [{"type": "function", "function": {"name": "test_tool"}}]},
    )

    with patch("httpx.AsyncClient") as mock_client:
        mock_client_instance = mock_client.return_value.__aenter__.return_value
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"prob_true": 0.8, "decision_id": 123}
        mock_client_instance.post = AsyncMock(return_value=mock_resp)

        decisions = await mock_router.get_ranked_models(
            request, strategy="expected_utility"
        )

        returned_model_ids = [d.model_id for d in decisions]
        assert "model-no-tools" not in returned_model_ids
        assert "model-tool-supporter" in returned_model_ids
        assert "claude-3-5-sonnet" in returned_model_ids


@pytest.mark.asyncio
async def test_sticky_routing(mock_router):
    request = RoutingRequest(
        query="Test sticky",
        agent_id="test_agent",
        parameters={
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "tool", "content": "tool result"},
            ]
        },
    )

    import hashlib
    import json

    first_msg_str = json.dumps(request.parameters["messages"][0], sort_keys=True)
    session_str = f"test_agent_{first_msg_str}"
    session_hash = hashlib.sha256(session_str.encode("utf-8")).hexdigest()
    session_id = f"zed_{session_hash}"
    import src.router.core as rc
    print("FILE:", rc.__file__)

    mock_router.active_sessions[session_id] = "model-tool-supporter"

    with patch.object(
        mock_router, "get_ranked_models", new_callable=AsyncMock
    ) as mock_get_ranked:
        mock_adapter = AsyncMock()
        mock_adapter.forward_request.return_value = {"text": "Response"}
        mock_router.adapters = {"model-tool-supporter": mock_adapter}

        mock_router.log_routing_decision = MagicMock()
        mock_router._get_semaphore = MagicMock(return_value=None)
        mock_router.extract_coding_features = MagicMock(return_value={})

        response = await mock_router.route_request(request)

        mock_get_ranked.assert_not_called()
        assert response.model_id == "model-tool-supporter"

        del mock_router.models["model-tool-supporter"]
        with pytest.raises(HTTPException) as exc_info:
            await mock_router.route_request(request)
        assert exc_info.value.status_code == 503
        assert "Sticky model unavailable" in exc_info.value.detail


@pytest.mark.asyncio
async def test_fallback_strip(mock_router):
    request = RoutingRequest(
        query="Test fallback",
        agent_id="test_agent",
        parameters={
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [{"type": "function", "function": {"name": "test_tool"}}],
            "tool_choice": "auto",
        },
    )

    with patch.object(
        mock_router, "get_ranked_models", new_callable=AsyncMock
    ) as mock_get_ranked:
        mock_get_ranked.return_value = [
            RoutingDecision(
                model_id="model-no-tools",
                name="No Tools",
                expected_utility=10.0,
                cost=0.01,
                time=1.0,
                probability=0.9,
            )
        ]

        mock_adapter = AsyncMock()
        mock_adapter.forward_request.return_value = {"text": "Fallback Response"}
        mock_router.adapters = {"model-no-tools": mock_adapter}

        mock_router.log_routing_decision = MagicMock()
        mock_router._get_semaphore = MagicMock(return_value=None)
        mock_router.extract_coding_features = MagicMock(return_value={})
        mock_router.assess_user_sentiment = AsyncMock(return_value="neutral")

        response = await mock_router.route_request(request)

        assert "tools" not in request.parameters
        assert "tool_choice" not in request.parameters
        assert len(request.parameters["messages"]) == 2
        assert request.parameters["messages"][0]["role"] == "system"
        assert (
            "The user has MCP tools available"
            in request.parameters["messages"][0]["content"]
        )
        assert response.model_id == "model-no-tools"


@pytest.mark.asyncio
async def test_zed_prioritization(mock_router):
    request = RoutingRequest(query="Test zed", agent_id="zed_editor_v1")

    with patch("httpx.AsyncClient") as mock_client:
        mock_client_instance = mock_client.return_value.__aenter__.return_value
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"prob_true": 0.8, "decision_id": 123}
        mock_client_instance.post = AsyncMock(return_value=mock_resp)

        mock_router.utility_calculator.calculate_expected_utility = MagicMock(
            return_value=10.0
        )

        decisions = await mock_router.get_ranked_models(
            request, strategy="expected_utility"
        )

        claude_decision = next(
            d for d in decisions if d.model_id == "claude-3-5-sonnet"
        )
        other_decision = next(
            d for d in decisions if d.model_id == "model-tool-supporter"
        )

        assert claude_decision.expected_utility == 12.0
        assert other_decision.expected_utility == 10.0
