"""Deterministic tests for RealitySignal fallback, circuit breaker, semaphore skip, and local LADDER."""

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.config.settings import get_settings
from src.models.routing import RoutingRequest
from src.router.load_balancer import LoadBalancer


class _AsyncClientCtx:
    def __init__(self, post_fn):
        self.post = post_fn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_reality_signal_fallback_5xx_timeout_and_401(base_router):
    """500, TimeoutException, and 401 fall back to prob=0.5; 401 invokes note_auth_failure."""
    calls = {"n": 0}

    async def post_fn(url, *args, **kwargs):
        idx = calls["n"]
        calls["n"] += 1
        if idx == 0:
            return httpx.Response(status_code=500, text="Internal Server Error")
        if idx == 1:
            raise httpx.TimeoutException("timeout")
        if idx == 2:
            return httpx.Response(status_code=401, json={"error": "unauthorized"})
        return httpx.Response(status_code=200, json={"prob_true": 0.5})

    req = RoutingRequest(
        query="fallback test",
        agent_id="resilience_agent",
        parameters={"messages": [{"role": "user", "content": "fallback test"}]},
    )
    with patch("src.router.core.httpx.AsyncClient", return_value=_AsyncClientCtx(post_fn)), \
         patch("src.router.core.note_auth_failure") as mock_auth_fail:
        ranked = await base_router.get_ranked_models(req, strategy="expected_utility")

    assert len(ranked) == 3
    for decision in ranked:
        assert decision.probability == 0.5
    mock_auth_fail.assert_called_once()


def test_load_balancer_circuit_breaker_lifecycle():
    """Full CLOSED -> OPEN -> HALF_OPEN -> CLOSED transition on a fresh LoadBalancer."""
    lb = LoadBalancer()
    lb.add_model("m1", "M1")

    assert lb.is_model_healthy("m1") is True
    for _ in range(5):
        lb.record_failure("m1")

    cb = lb.circuit_breakers["m1"]
    assert cb["state"] == "OPEN"
    assert cb["failure_count"] == 5
    assert lb.is_model_healthy("m1") is False

    cb["last_failure_time"] = time.time() - 60.0
    assert lb.is_model_healthy("m1") is False
    assert cb["state"] == "HALF_OPEN"
    assert lb.is_model_healthy("m1") is True

    lb.record_success("m1")
    assert cb["state"] == "CLOSED"
    assert cb["failure_count"] == 0


@pytest.mark.asyncio
async def test_concurrency_semaphore_skips_saturated_model(base_router):
    """When top-ranked model's semaphore is saturated, route_request skips it and routes to #2."""
    base_router.utility_calculator.reward = 10.0
    base_router.models["gemini-3.1-flash-lite"]["concurrency_limit"] = 1

    a_lite = AsyncMock()
    a_lite.forward_request.return_value = {"text": "from-lite", "usage": {"prompt_tokens": 2, "completion_tokens": 2}}
    a_flash = AsyncMock()
    a_flash.forward_request.return_value = {"text": "from-flash", "usage": {"prompt_tokens": 2, "completion_tokens": 2}}
    base_router.adapters["gemini-3.1-flash-lite"] = a_lite
    base_router.adapters["gemini-2.5-flash"] = a_flash

    async def post_fn(url, json=None, **kwargs):
        mid = (json or {}).get("features", {}).get("model_id", "")
        probs = {
            "gemini-3.1-flash-lite": 0.99,
            "gemini-2.5-flash": 0.90,
            "gemini-3.5-flash": 0.10,
        }
        return httpx.Response(status_code=200, json={"prob_true": probs.get(mid, 0.5), "decision_id": 701})

    req = RoutingRequest(
        query="concurrency skip",
        agent_id="sem_agent",
        parameters={"messages": [{"role": "user", "content": "concurrency skip"}]},
    )

    sem = base_router._get_semaphore("gemini-3.1-flash-lite")
    assert sem is not None

    with patch("src.router.core.httpx.AsyncClient", return_value=_AsyncClientCtx(post_fn)):
        async with sem:
            rsp = await base_router.route_request(req, strategy="expected_utility")

    assert rsp.model_id == "gemini-2.5-flash"
    assert a_lite.forward_request.call_count == 0
    assert a_flash.forward_request.call_count == 1


@pytest.mark.asyncio
async def test_local_reality_signal_health_smoke():
    """Read-only health check against immutable local SNAP (18081) and LADDER (18082)."""
    async with httpx.AsyncClient(http2=False, trust_env=False) as client:
        for port in (18081, 18082):
            resp = await client.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
            assert resp.status_code == 200
            assert resp.json().get("status") == "healthy"


@pytest.mark.asyncio
async def test_local_ladder_multi_turn_escalation(base_router):
    """Turn 1 stops on cheapest model (0.95); Turn 2 escalates (0.25 -> 0.95) via local 18081/18082 URLs."""
    settings = get_settings()
    settings.reality_routing_url = "http://127.0.0.1:18081"
    settings.reality_rerouting_url = "http://127.0.0.1:18082"
    base_router.utility_calculator.reward = 100.0

    captured_urls = []
    ladder_probs = iter([0.95, 0.25, 0.95])

    async def post_fn(url, json=None, **kwargs):
        captured_urls.append(url)
        if "127.0.0.1:18081" in url:
            return httpx.Response(status_code=200, json={"prob_true": 0.5, "decision_id": 801})
        if "127.0.0.1:18082" in url:
            return httpx.Response(status_code=200, json={"prob_true": next(ladder_probs), "decision_id": 802})
        return httpx.Response(status_code=200, json={"prob_true": 0.5, "decision_id": 803})

    req1 = RoutingRequest(
        query="Explain 1+1",
        agent_id="ladder_multi_turn",
        parameters={"messages": [{"role": "user", "content": "Explain 1+1"}]},
    )
    req2 = RoutingRequest(
        query="Write a compiler",
        agent_id="ladder_multi_turn",
        parameters={"messages": [{"role": "user", "content": "Write a compiler"}]},
    )

    with patch("src.router.core.httpx.AsyncClient", return_value=_AsyncClientCtx(post_fn)):
        rsp1 = await base_router.route_request(req1, strategy="tiered_assessment")
        rsp2 = await base_router.route_request(req2, strategy="tiered_assessment")

    assert rsp1.model_id == "gemini-3.1-flash-lite"
    assert rsp2.model_id == "gemini-2.5-flash"
    assert any("127.0.0.1:18081/decide" in u for u in captured_urls)
    assert any("127.0.0.1:18082/decide" in u for u in captured_urls)
