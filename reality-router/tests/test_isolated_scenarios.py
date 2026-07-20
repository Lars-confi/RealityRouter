import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.models.routing import RoutingRequest, RoutingResponse
from src.router.core import RouterCore, RoutingDecision
from src.config.settings import get_settings, reload_settings


class MockHTTPXResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)

    def json(self):
        return self._json_data


@pytest.fixture(autouse=True)
def clean_environment():
    """Ensure that the local environment points to the isolated test directory."""
    # This is set up by run_isolated_tests.py, but we guarantee it here too
    assert "REALITY_ROUTER_HOME" in os.environ, "Tests must run inside an isolated REALITY_ROUTER_HOME sandbox!"
    # Ensure settings are loaded fresh for each test
    reload_settings()
    yield


@pytest.fixture
def mock_db():
    with patch("src.router.core.SessionLocal") as mock_session_local:
        db = MagicMock()
        mock_session_local.return_value = db
        yield db


@pytest.fixture
def router_core(mock_db):
    """Provide a mocked RouterCore instance."""
    core = RouterCore()
    # Force mock models in the active pool
    core.models = {
        "gemini-2.5-flash": {
            "name": "Gemini 2.5 Flash",
            "cost": 0.001,
            "time": 1.0,
            "probability": 0.8,
            "supports_function_calling": True,
        },
        "gemini-3.1-flash-lite": {
            "name": "Gemini 3.1 Flash Lite",
            "cost": 0.0005,
            "time": 0.5,
            "probability": 0.75,
            "supports_function_calling": True,
        },
        "gemini-3.5-flash": {
            "name": "Gemini 3.5 Flash",
            "cost": 0.002,
            "time": 1.2,
            "probability": 0.85,
            "supports_function_calling": True,
        },
    }
    
    # Configure mock adapters
    mock_adapter = AsyncMock()
    mock_adapter.forward_request.return_value = {"text": "Success Response"}
    core.adapters = {
        "gemini-2.5-flash": mock_adapter,
        "gemini-3.1-flash-lite": mock_adapter,
        "gemini-3.5-flash": mock_adapter,
    }
    
    # Pre-populate discovered models list
    core.all_discovered_models = [
        {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "provider": "gemini", "enabled": True},
        {"id": "gemini-3.1-flash-lite", "name": "Gemini 3.1 Flash Lite", "provider": "gemini", "enabled": True},
        {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash", "provider": "gemini", "enabled": True},
    ]
    
    # Mock capability manager & load balancer
    core.load_balancer.add_model("gemini-2.5-flash", "Gemini 2.5 Flash", 1.0)
    core.load_balancer.add_model("gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite", 1.0)
    core.load_balancer.add_model("gemini-3.5-flash", "Gemini 3.5 Flash", 1.0)
    core.load_balancer.is_model_healthy = MagicMock(return_value=True)
    
    return core


@pytest.mark.asyncio
async def test_scenario_a_snap_google_sso(router_core):
    """
    Scenario A: Test Snap (expected_utility) strategy with Google SSO.
    Verifies that X-Reality-Check-Token is used instead of Authorization,
    and that the token is correctly formatted with the Bearer prefix.
    """
    settings = get_settings()
    # Explicitly mock settings values
    settings.default_strategy = "expected_utility"
    settings.reality_check_token = "Bearer google_token_123"
    settings.reality_check_provider = "Google"

    request = RoutingRequest(
        query="Test query for Snap strategy",
        agent_id="test_agent",
        parameters={"messages": [{"role": "user", "content": "Test query"}]}
    )

    async def mock_rc_post(url, json=None, headers=None, timeout=None, **kwargs):
        assert headers is not None, "Headers must be provided!"
        # Verify Snap URL is used
        assert "snap-api" in url
        # Verify X-Reality-Check-Token bypass header is sent (NOT Authorization)
        assert "X-Reality-Check-Token" in headers
        assert "Authorization" not in headers
        # Verify token value is well-formed with Bearer prefix
        assert headers["X-Reality-Check-Token"] == "Bearer google_token_123"
        return MockHTTPXResponse({"prob_true": 0.9, "decision_id": 100})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post) as mock_post:
        decisions = await router_core.get_ranked_models(request, strategy="expected_utility")
        # Ensure we got decisions ranked by expected utility
        assert len(decisions) == 3
        assert decisions[0].probability == 0.9
        assert mock_post.call_count == 3


@pytest.mark.asyncio
async def test_scenario_b_ladder_github_sso(router_core):
    """
    Scenario B: Test Ladder (tiered_assessment) strategy with GitHub SSO.
    Verifies that the standard Authorization header is used to pass through
    Azure Easy Auth, and that escalation crawls models from cheapest to most expensive.
    """
    settings = get_settings()
    settings.default_strategy = "tiered_assessment"
    settings.reality_check_token = "Bearer github_token_123"
    settings.reality_check_provider = "GitHub"

    request = RoutingRequest(
        query="Test query for Ladder",
        agent_id="test_agent",
        parameters={
            "messages": [
                {"role": "user", "content": "A very hard coding question"}
            ]
        }
    )

    async def mock_rc_post(url, json=None, headers=None, timeout=None, **kwargs):
        assert headers is not None, "Headers must be provided!"
        # Verify standard Authorization header is sent (Easy Auth whitelists GitHub)
        assert "Authorization" in headers
        assert "X-Reality-Check-Token" not in headers
        assert headers["Authorization"] == "Bearer github_token_123"
        
        # Mock decider response
        if "snap-api" in url:
            return MockHTTPXResponse({"prob_true": 0.5, "decision_id": 201}) # Low initial confidence
        else:
            # Ladder api post-hoc assessment
            assert "ladder-api" in url
            return MockHTTPXResponse({"prob_true": 0.95, "decision_id": 202}) # High post-hoc confidence

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post) as mock_post:
        response = await router_core.route_request(request, strategy="tiered_assessment")
        assert response.model_id is not None
        
        # Ladder should sort cheapest first:
        # 1. gemini-3.1-flash-lite (Cost: 0.0005) -> Evaluated first
        # 2. gemini-2.5-flash (Cost: 0.001)
        # 3. gemini-3.5-flash (Cost: 0.002)
        # Since the mock initially returned low confidence (0.5), it should have attempted escalation!
        assert mock_post.call_count >= 2


@pytest.mark.asyncio
async def test_whitelist_filtering(router_core):
    """
    Scenario C: Verify model whitelisting/blacklisting.
    Ensures that disabled models are never registered in the active routing pool.
    """
    settings = get_settings()
    # Mock gemma as disabled
    settings.disabled_models = ["gemini-3.1-flash-lite"]
    
    # Reload router core with new settings
    core = RouterCore()
    
    # Populate the config list to simulate discovered models
    core.all_discovered_models = [
        {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "provider": "gemini", "enabled": True},
        {"id": "gemini-3.1-flash-lite", "name": "Gemini 3.1 Flash Lite", "provider": "gemini", "enabled": False},
    ]
    
    # Trigger loading (it should skip gemini-3.1-flash-lite since it is in settings.disabled_models)
    with patch("src.router.core.load_models_from_config", return_value={
        "gemini-2.5-flash": {"name": "Gemini 2.5 Flash"},
        "gemini-3.1-flash-lite": {"name": "Gemini 3.1 Flash Lite"}
    }):
        core.load_configured_models()
        
    # Gemini 3.1 Flash Lite should NOT be in core.models pool
    assert "gemini-2.5-flash" in core.models
    assert "gemini-3.1-flash-lite" not in core.models
