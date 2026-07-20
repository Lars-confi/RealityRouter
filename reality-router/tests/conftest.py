import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.config.settings import get_settings, reload_settings
from src.router.core import RouterCore


class MockHTTPXResponse:
    """Helper class to mock httpx responses."""
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)

    def json(self):
        return self._json_data


@pytest.fixture(autouse=True)
def clean_environment():
    """Ensure that the local environment points to the isolated test directory."""
    assert "REALITY_ROUTER_HOME" in os.environ, "Tests must run inside an isolated REALITY_ROUTER_HOME sandbox!"
    reload_settings()
    yield


@pytest.fixture
def mock_db():
    """Mock the database session for standard DB operations."""
    with patch("src.router.core.SessionLocal") as mock_session_local:
        db = MagicMock()
        # Mock simple query chain: db.query().filter().first() etc.
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = []
        mock_query.first.return_value = None
        db.query.return_value = mock_query
        
        mock_session_local.return_value = db
        yield db


@pytest.fixture
def base_router(mock_db):
    """Provide a standard configured RouterCore instance with three default models."""
    core = RouterCore()
    core.utility_calculator.reward = 1.0
    
    # Clean populate of models
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
    
    # Setup load balancer
    core.load_balancer.models = {}
    core.load_balancer.add_model("gemini-2.5-flash", "Gemini 2.5 Flash", 1.0)
    core.load_balancer.add_model("gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite", 1.0)
    core.load_balancer.add_model("gemini-3.5-flash", "Gemini 3.5 Flash", 1.0)
    core.load_balancer.is_model_healthy = MagicMock(return_value=True)
    
    return core
