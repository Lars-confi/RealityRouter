import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from sqlalchemy.pool import NullPool

from src.main import app
from src.models.database import DATABASE_URL, engine
from src.router.core import router_core


class MockHTTPXResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)

    def json(self):
        return self._json_data


@pytest.fixture(autouse=True)
def restore_router_state():
    saved_models = dict(router_core.models)
    saved_adapters = dict(router_core.adapters)
    saved_lb = router_core.load_balancer.is_model_healthy
    saved_sessions = dict(router_core.active_sessions)
    try:
        yield
    finally:
        router_core.models = saved_models
        router_core.adapters = saved_adapters
        router_core.load_balancer.is_model_healthy = saved_lb
        router_core.active_sessions.clear()
        router_core.active_sessions.update(saved_sessions)


def test_file_sqlite_engine_uses_nullpool():
    """File-backed SQLite must use NullPool so concurrent async requests cannot starve QueuePool(5, 10)."""
    if "sqlite" in DATABASE_URL and ":memory:" not in DATABASE_URL:
        assert isinstance(engine.pool, NullPool)


@pytest.mark.asyncio
async def test_concurrent_burst_chat_completions_wal_and_pooled_client():
    """16 parallel /v1/chat/completions requests complete cleanly with WAL logging and pooled client reuse."""
    router_core.models = {
        "burst-model-alpha": {
            "name": "Burst Model Alpha",
            "cost": 0.001,
            "time": 0.5,
            "probability": 0.9,
            "supports_function_calling": True,
        },
        "burst-model-beta": {
            "name": "Burst Model Beta",
            "cost": 0.002,
            "time": 0.8,
            "probability": 0.85,
            "supports_function_calling": True,
        },
    }

    async def forward_alpha(*args, **kwargs):
        await asyncio.sleep(0.01)
        return {
            "text": "Burst model alpha completed successfully.",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    async def forward_beta(*args, **kwargs):
        await asyncio.sleep(0.01)
        return {
            "text": "Burst model beta completed successfully.",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    adapter_alpha = AsyncMock()
    adapter_alpha.forward_request = AsyncMock(side_effect=forward_alpha)
    adapter_beta = AsyncMock()
    adapter_beta.forward_request = AsyncMock(side_effect=forward_beta)
    router_core.adapters = {
        "burst-model-alpha": adapter_alpha,
        "burst-model-beta": adapter_beta,
    }
    router_core.load_balancer.is_model_healthy = MagicMock(return_value=True)

    await router_core.close_http_client()

    rc_calls = 0
    seen_client_ids = set()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        reset_r = await async_client.post("/metrics/reset")
        assert reset_r.status_code == 200

        pooled = router_core._get_rc_http_client()

        async def patched_post(url, *args, **kwargs):
            nonlocal rc_calls
            rc_calls += 1
            seen_client_ids.add(id(router_core._rc_http_client))
            await asyncio.sleep(0.005)
            return MockHTTPXResponse({"prob_true": 0.9, "decision_id": rc_calls})

        with patch.object(pooled, "post", new=patched_post):
            async def send_one(idx: int):
                return await async_client.post(
                    "/v1/chat/completions",
                    headers={
                        "Authorization": "Bearer rr-local",
                        "User-Agent": f"BurstClient/{idx % 4}",
                    },
                    json={
                        "model": "auto",
                        "messages": [{"role": "user", "content": f"Burst request number {idx}"}],
                    },
                )

            responses = await asyncio.gather(*(send_one(i) for i in range(16)))

        assert len(responses) == 16
        assert all(r.status_code == 200 for r in responses), [
            (r.status_code, r.text[:160]) for r in responses if r.status_code != 200
        ]
        for r in responses:
            body = r.json()
            assert body["choices"][0]["message"]["content"].startswith("Burst model ")

        assert rc_calls >= 16
        assert len(seen_client_ids) == 1

        summary_r = await async_client.get("/metrics/summary")
        assert summary_r.status_code == 200
        summary = summary_r.json()
        assert summary["total_requests"] == 16
        assert sum(a["requests"] for a in summary["agents"].values()) == 16

        history_r = await async_client.get("/metrics/history?limit=50")
        assert history_r.status_code == 200
        assert len(history_r.json()) == 16

    await router_core.close_http_client()
