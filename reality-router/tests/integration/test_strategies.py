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
    base_router.utility_calculator.reward = 10.0
    request = RoutingRequest(
        query="Write a quick script",
        agent_id="test_agent",
        parameters={"messages": [{"role": "user", "content": "Write a script"}]}
    )

    # We mock the decider API so that gemini-3.1-flash-lite has high utility
    async def mock_rc_post(url, json=None, headers=None, **kwargs):
        model_id = json.get("features", {}).get("model_id", "")
        probs = {
            "gemini-3.1-flash-lite": 0.99,
            "gemini-2.5-flash": 0.1,
            "gemini-3.5-flash": 0.1,
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
    base_router.utility_calculator.reward = 10.0
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
    base_router.utility_calculator.reward = 10.0
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


@pytest.mark.asyncio
async def test_vision_modality_aware_routing(base_router):
    """
    Verifies that when a request contains an image (type: "image_url"),
    only models supporting vision are evaluated.
    """
    # Create request with image
    request = RoutingRequest(
        query="Analyze this image",
        agent_id="test_agent",
        parameters={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image?"},
                        {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}
                    ]
                }
            ]
        }
    )

    # Let's mock get_model_capabilities to return supports_vision = True for gemini-3.5-flash
    # and supports_vision = False for gemini-3.1-flash-lite and gemini-2.5-flash.
    def mock_get_model_capabilities(model_name: str):
        if "gemini-3.5-flash" in model_name:
            return {"supports_vision": True}
        return {"supports_vision": False}

    async def mock_rc_post(url, json=None, headers=None, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 999})

    with patch("src.utils.pricing.pricing_manager.get_model_capabilities", side_effect=mock_get_model_capabilities), \
         patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        # We run the expected_utility (Snap) strategy
        response = await base_router.route_request(request, strategy="expected_utility")
        
        # Should select gemini-3.5-flash because it's the only one that supports vision
        assert response.model_id == "gemini-3.5-flash"



@pytest.mark.asyncio
async def test_routing_max_tokens_clamping(base_router):
    """
    Verifies that when a request is made with a high max_tokens or max_completion_tokens value,
    it is clamped to the model's configured max_tokens cap.
    """
    # 1. Setup gemini-3.5-flash's max_tokens as 4096 in the router's model configuration
    base_router.models["gemini-3.5-flash"]["max_tokens"] = 4096

    # 2. Make a request with max_tokens=32000
    request = RoutingRequest(
        query="Write a long story",
        agent_id="test_agent",
        parameters={
            "messages": [{"role": "user", "content": "Write a long story"}],
            "max_tokens": 32000,
            "max_completion_tokens": 32000,
        }
    )

    # 3. We mock the decider API so gemini-3.5-flash is selected (highest probability)
    async def mock_rc_post(url, json=None, headers=None, **kwargs):
        # Return high probability for gemini-3.5-flash
        model_id = json.get("features", {}).get("model_id", "") if json else ""
        prob = 0.99 if model_id == "gemini-3.5-flash" else 0.1
        return MockHTTPXResponse({"prob_true": prob, "decision_id": 1001})

    # Get the mock adapter for gemini-3.5-flash and reset it
    adapter = base_router.adapters["gemini-3.5-flash"]
    adapter.forward_request.reset_mock()
    adapter.forward_request.return_value = {"text": "Success Response"}

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        # Route the request using expected_utility
        await base_router.route_request(request, strategy="expected_utility")

        # 4. Assert that the request passed to the adapter has been successfully clamped
        adapter.forward_request.assert_called_once()
        called_args, called_kwargs = adapter.forward_request.call_args
        clamped_request = called_args[0]
        
        # Verify the parameters were clamped
        assert clamped_request.parameters["max_tokens"] == 4096
        assert clamped_request.parameters["max_completion_tokens"] == 4096
