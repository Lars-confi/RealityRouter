import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.models.routing import RoutingRequest


class MockHTTPXResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)

    def json(self):
        return self._json_data


@pytest.mark.asyncio
async def test_snap_strategy_sorting_and_routing(base_router):
    """
    Verifies the Snap strategy (expected_utility):
    1. Ranks all available models using EUT (Expected Utility Theory).
    2. Sorts models by utility descending.
    3. Selects the highest-utility model and routes the prompt.
    """
    request = RoutingRequest(
        query="Write a quick script",
        agent_id="test_agent",
        parameters={"messages": [{"role": "user", "content": "Write a script"}]}
    )

    # We mock the decider API so that:
    # - gemini-3.1-flash-lite: prob = 0.8
    # - gemini-2.5-flash: prob = 0.85
    # - gemini-3.5-flash: prob = 0.9
    async def mock_rc_post(url, json=None, headers=None, **kwargs):
        model_id = json.get("features", {}).get("model_id", "")
        probs = {
            "gemini-3.1-flash-lite": 0.8,
            "gemini-2.5-flash": 0.85,
            "gemini-3.5-flash": 0.9,
        }
        return MockHTTPXResponse({"prob_true": probs.get(model_id, 0.5), "decision_id": 601})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        # We run the expected_utility (Snap) strategy
        response = await base_router.route_request(request, strategy="expected_utility")
        
        # gemini-3.1-flash-lite is the cheapest (0.0005) and fastest (0.5), yielding the highest Expected Utility
        # even though its probability is slightly lower (0.8) than gemini-3.5-flash (0.9, cost 0.002, time 1.2).
        assert response.model_id == "gemini-3.1-flash-lite"


@pytest.mark.asyncio
async def test_ladder_strategy_success_on_first_try(base_router):
    """
    Verifies the Ladder strategy (tiered_assessment) when the first (cheapest) model succeeds:
    1. Sorts candidate models cheapest-first.
    2. Runs the cheapest model (gemini-3.1-flash-lite).
    3. The post-hoc assessment returns high confidence (e.g. 0.95).
    4. The router accepts the answer and stops, without escalating to more expensive models.
    """
    request = RoutingRequest(
        query="Explain 1+1",
        agent_id="test_agent",
        parameters={"messages": [{"role": "user", "content": "Explain 1+1"}]}
    )

    async def mock_rc_post(url, json=None, headers=None, **kwargs):
        # Post-hoc decider returns very high confidence for first model
        return MockHTTPXResponse({"prob_true": 0.95, "decision_id": 602})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post) as mock_post:
        response = await base_router.route_request(request, strategy="tiered_assessment")
        
        # Should stop at the cheapest model
        assert response.model_id == "gemini-3.1-flash-lite"
        # 3 calls to Snap API (to calibrate priors) + 1 call to Ladder API (post-hoc) = 4 calls total
        assert mock_post.call_count == 4


@pytest.mark.asyncio
async def test_ladder_strategy_escalation_flow(base_router):
    """
    Verifies the Ladder strategy (tiered_assessment) escalation flow:
    1. Runs the cheapest model (gemini-3.1-flash-lite).
    2. Post-hoc validator returns low confidence (e.g. 0.4).
    3. The stopping condition u_stop < eu_continue is met (escalation triggers).
    4. The router runs the next best model (gemini-2.5-flash).
    5. The post-hoc validator returns high confidence (e.g. 0.95).
    6. Router stops at the second tier, returning the second model's response.
    """
    request = RoutingRequest(
        query="Write a complex compiler",
        agent_id="test_agent",
        parameters={"messages": [{"role": "user", "content": "Write a compiler"}]}
    )

    # Mock responses:
    # Turn 1: gemini-3.1-flash-lite (cheapest) -> returns low post-hoc confidence (0.4)
    # Turn 2: gemini-2.5-flash (next cheapest) -> returns high post-hoc confidence (0.95)
    call_counts = {"snap": 0, "ladder": 0}
    
    async def mock_rc_post(url, json=None, headers=None, **kwargs):
        if "snap-api" in url:
            call_counts["snap"] += 1
            return MockHTTPXResponse({"prob_true": 0.5, "decision_id": 603})
        else:
            call_counts["ladder"] += 1
            prob = 0.4 if call_counts["ladder"] == 1 else 0.95
            return MockHTTPXResponse({"prob_true": prob, "decision_id": 604})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = await base_router.route_request(request, strategy="tiered_assessment")
        
        # Should escalate past gemini-3.1-flash-lite and stop at gemini-2.5-flash
        assert response.model_id == "gemini-2.5-flash"
        assert call_counts["ladder"] == 2 # Evaluated two models post-hoc
