import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from src.main import app
from src.models.database import SessionLocal, RoutingLog, init_db
from src.models.routing import RoutingRequest
from src.router.core import RouterCore


class MockHTTPXResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)

    def json(self):
        return self._json_data


@pytest.fixture
def test_client():
    """Provides a standard FastAPI TestClient for synchronous API testing."""
    return TestClient(app)


def test_dashboard_html_rendering(test_client):
    """
    Verifies that the /metrics/dashboard endpoint:
    1. Returns an HTTP 200 OK.
    2. Renders valid HTML content.
    3. Contains key control center components (Dashboard title, Performance tab, Settings tab, Sliders).
    """
    response = test_client.get("/metrics/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    
    html = response.text
    # Verify key structural components exist in the returned HTML template
    assert "REALITY ROUTER CONTROL CENTER" in html
    assert "Model Performance &amp; Unit Economics" in html or "Model Performance & Unit Economics" in html
    assert "summary-grid" in html
    assert "models-table" in html
    assert "pref-slider" in html


def test_dashboard_models_all_endpoint(test_client, base_router):
    """
    Verifies that the /metrics/models/all endpoint:
    1. Correctly reads and serializes active models.
    2. Returns correct active/disabled status categories.
    3. Incorporates details like prompt/completion costs.
    """
    # Patch the router core global instance so the endpoint reads from our base_router fixture
    with patch("src.router.core.router_core", base_router):
        response = test_client.get("/metrics/models/all")
        assert response.status_code == 200
        
        data = response.json()
        assert "models" in data
        models = data["models"]
        
        # We configured 3 models in our base_router fixture:
        # gemini-2.5-flash, gemini-3.1-flash-lite, gemini-3.5-flash
        assert len(models) == 3
        
        # Verify schema is populated correctly
        for m in models:
            assert "id" in m
            assert "name" in m
            assert "provider" in m
            assert "status_category" in m
            assert m["status_category"] == "active"
            
            details = m.get("details")
            assert details is not None
            assert "prompt_cost" in details
            assert "completion_cost" in details


def test_dashboard_metrics_summary_endpoint(test_client, mock_db):
    """
    Verifies that the /metrics/summary endpoint:
    1. Connects to the database successfully.
    2. Returns a valid JSON summarizing costs, latency, and requests.
    3. Aggregates data correctly even when there is no history yet.
    """
    response = test_client.get("/metrics/summary")
    assert response.status_code == 200
    
    data = response.json()
    assert "total_requests" in data
    assert "total_cost" in data
    assert "potential_max_cost" in data
    assert "models" in data
    
    # Costs should default to 0 if there's no data in the mock DB
    assert data["total_requests"] == 0
    assert data["total_cost"] == 0.0


def test_dashboard_api_key_alert(test_client, mock_db):
    """
    Verifies that if there is an API key or authentication failure:
    1. The backend detects the failure pattern.
    2. The /metrics/summary endpoint exposes an alert with the model details.
    3. The front-end renders the warning banner containing the error details.
    """
    # Mock a failed RoutingLog entry containing an API key error
    mock_failed_log = MagicMock()
    mock_failed_log.success = False
    mock_failed_log.model_id = "gemini-3.5-flash"
    mock_failed_log.model_name = "Gemini 3.5 Flash"
    mock_failed_log.response_payload = "Incorrect API key provided: invalid_api_key"
    mock_failed_log.timestamp = None
    mock_failed_log.cost = 0.0
    mock_failed_log.time = 0.5
    mock_failed_log.prompt_tokens = 0
    mock_failed_log.completion_tokens = 0
    mock_failed_log.total_tokens = 0
    mock_failed_log.expected_utility = 0.0
    mock_failed_log.user_sentiment = None
    mock_failed_log.routing_context = None
    mock_failed_log.probability = 0.0
    mock_failed_log.agent_id = "default"
    mock_failed_log.potential_cost = 0.0

    # Instruct the mock DB to return this failed log
    mock_db.query.return_value.all.return_value = [mock_failed_log]
    mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [mock_failed_log]

    response = test_client.get("/metrics/summary")
    assert response.status_code == 200
    
    data = response.json()
    assert "api_key_alerts" in data
    assert data["api_key_alerts"] is not None
    assert len(data["api_key_alerts"]) == 1
    
    alert = data["api_key_alerts"][0]
    assert alert["model_id"] == "gemini-3.5-flash"
    assert "Incorrect API key" in alert["error_message"]

    # Also verify that the HTML dashboard contains the warning container (#api-alert)
    html_response = test_client.get("/metrics/dashboard")
    assert html_response.status_code == 200
    assert "id=\"api-alert\"" in html_response.text


def test_dashboard_time_windowing(test_client, mock_db):
    """
    Verifies that the /metrics/summary?window=... endpoint:
    1. Successfully applies filter and parses query parameters.
    2. Works smoothly for different time intervals ('24h', '7d', '30d').
    """
    for interval in ["24h", "7d", "30d", "all"]:
        response = test_client.get(f"/metrics/summary?window={interval}")
        assert response.status_code == 200


def test_dashboard_reset_endpoint(test_client, mock_db):
    """
    Verifies that the /metrics/reset endpoint:
    1. Returns HTTP 200 OK.
    2. Triggers delete transactions against the SQLite database.
    """
    response = test_client.post("/metrics/reset")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    # Verify that the DB query deletes were actually executed
    assert mock_db.query.return_value.delete.call_count == 2
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_long_query_potential_cost_persistence(test_client):
    """
    Verifies that for a long query (>1500 chars):
    1. A router pool with a free model (cost=0.0) and an expensive model (cost=0.02) is configured.
    2. Submits a RoutingRequest with a query longer than 1500 characters.
    3. Verifies that RoutingLog.potential_cost in the database is strictly greater than 0.0.
    4. Verifies that /metrics/summary calculates positive savings!
    """
    init_db()
    db = SessionLocal()
    try:
        db.query(RoutingLog).delete()
        db.commit()
    finally:
        db.close()

    router = RouterCore()
    router.utility_calculator.reward = 10.0
    router.models = {
        "free-model": {
            "name": "Free Local Model",
            "cost": 0.0,
            "prompt_cost": 0.0,
            "completion_cost": 0.0,
            "time": 0.5,
            "probability": 0.95,
            "supports_function_calling": True,
        },
        "expensive-model": {
            "name": "Expensive Flagship Model",
            "cost": 0.02,
            "prompt_cost": 0.02,
            "completion_cost": 0.02,
            "time": 1.0,
            "probability": 0.95,
            "supports_function_calling": True,
        },
    }

    mock_adapter = AsyncMock()
    mock_adapter.forward_request.return_value = {
        "text": "Success Response",
        "usage": {
            "prompt_tokens": 600,
            "completion_tokens": 100,
            "total_tokens": 700,
        },
    }
    router.adapters = {
        "free-model": mock_adapter,
        "expensive-model": mock_adapter,
    }

    router.load_balancer.models = {}
    router.load_balancer.add_model("free-model", "Free Local Model", 1.0)
    router.load_balancer.add_model("expensive-model", "Expensive Flagship Model", 1.0)
    router.load_balancer.is_model_healthy = MagicMock(return_value=True)

    long_query = "Explain reality router architecture in detail. " + ("detailed explanation of routing logic " * 50)
    assert len(long_query) > 1500

    request = RoutingRequest(
        query=long_query,
        agent_id="test_long_query_agent",
        parameters={"messages": [{"role": "user", "content": long_query}]},
    )

    async def mock_rc_post(url, json=None, headers=None, **kwargs):
        return MockHTTPXResponse({"prob_true": 0.95, "decision_id": 1234})

    with patch("httpx.AsyncClient.post", side_effect=mock_rc_post):
        response = await router.route_request(request, strategy="expected_utility")

    assert response.model_id == "free-model"

    # Verify database persistence
    db = SessionLocal()
    try:
        log_entry = (
            db.query(RoutingLog)
            .filter(RoutingLog.agent_id == "test_long_query_agent")
            .order_by(RoutingLog.timestamp.desc())
            .first()
        )
        assert log_entry is not None
        assert log_entry.cost == 0.0
        assert log_entry.potential_cost > 0.0
    finally:
        db.close()

    # Verify /metrics/summary calculates positive savings
    summary_resp = test_client.get("/metrics/summary")
    assert summary_resp.status_code == 200
    summary_data = summary_resp.json()
    assert summary_data["total_requests"] >= 1
    assert summary_data["potential_max_cost"] > summary_data["total_cost"]
    savings = summary_data["potential_max_cost"] - summary_data["total_cost"]
    assert savings > 0.0
