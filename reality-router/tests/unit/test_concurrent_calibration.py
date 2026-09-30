import asyncio
import time
from unittest.mock import patch, MagicMock

import pytest

from tests.conftest import MockHTTPXResponse
from src.models.routing import RoutingRequest


@pytest.mark.asyncio
async def test_concurrent_snap_calibration(router_core):
    # Setup 5 models
    router_core.models = {
        f"model-{i}": {
            "name": f"Model {i}",
            "cost": 0.001 * i,
            "time": 0.5 + 0.1 * i,
            "probability": 0.7,
            "supports_function_calling": True,
        }
        for i in range(1, 6)
    }

    # Ensure all 5 models are considered healthy
    router_core.load_balancer.is_model_healthy = MagicMock(return_value=True)

    # Mock httpx.AsyncClient.post with an async sleep simulating latency
    async def mock_post(*args, **kwargs):
        await asyncio.sleep(0.08)
        return MockHTTPXResponse(
            {
                "prob": 0.85,
                "uncertainty": 0.05,
                "decision_id": "decision-123",
                "feedback_requested": False,
            },
            status_code=200,
        )

    request = RoutingRequest(
        query="Test concurrent calibration query",
        parameters={"messages": [{"role": "user", "content": "Test concurrent calibration query"}]},
    )

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        start_time = time.time()
        decisions = await router_core.get_ranked_models(request)
        total_time = time.time() - start_time

    # 5 models * 0.08s = 0.40s if sequential. In parallel, it should be ~0.08-0.12s (< 0.25s).
    assert total_time < 0.25, f"Calibration took {total_time:.3f}s, expected < 0.25s (concurrent)"

    # Assert all 5 decisions are returned with their calibrated scores
    assert len(decisions) == 5
    returned_model_ids = {d.model_id for d in decisions}
    assert returned_model_ids == {f"model-{i}" for i in range(1, 6)}
    for d in decisions:
        assert d.probability == 0.85
        assert d.reality_check_id == "decision-123"
        assert d.uncertainty == 0.05
