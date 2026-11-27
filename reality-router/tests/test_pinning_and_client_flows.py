import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from src.router.core import RouterCore
from src.config.settings import get_settings
from src.models.routing import RoutingRequest
from fastapi.exceptions import HTTPException

# Reuse the MockHTTPXResponse pattern from conftest.py
class MockHTTPXResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code
        self.text = str(json_data)

    def json(self):
        return self._json_data


@pytest.mark.asyncio
async def test_tier_cheap_excludes_above_median_models(base_router):
    """1. model='tier:cheap' ranks only cheap/median models and ignores flagship even if high prob."""
    probs = {
        "gemini-3.5-flash": 0.99,
        "gemini-2.5-flash": 0.60,
        "gemini-3.1-flash-lite": 0.60,
    }

    async def mock_rs_post(url, *args, **kwargs):
        for mid, p in probs.items():
            if mid in url:
                return MockHTTPXResponse({"prob_true": p, "decision_id": 100})
        return MockHTTPXResponse({"prob_true": 0.0, "decision_id": 0})

    with patch("httpx.AsyncClient.post", side_effect=mock_rs_post):
        # Test get_ranked_models filtering
        request = RoutingRequest(
            query="Test expensive tier constraint",
            parameters={"model": "tier:cheap"},
        )
        ranked = await base_router.get_ranked_models(request, strategy="expected_utility")
        ranked_ids = [m.model_id for m in ranked]

        assert "gemini-3.5-flash" not in ranked_ids
        assert "gemini-2.5-flash" in ranked_ids
        assert "gemini-3.1-flash-lite" in ranked_ids

        # Test route_request selection within same patch context
        request2 = RoutingRequest(
            query="Test expensive tier constraint routed",
            parameters={"model": "tier:cheap"},
        )
        selected = await base_router.route_request(request2, strategy="expected_utility")
        assert selected.model_id != "gemini-3.5-flash"


@pytest.mark.asyncio
async def test_tier_flagship_selects_only_above_median_models(base_router):
    """2. model='tier:flagship' ranks only above-median models and ignores cheap ones."""
    probs = {
        "gemini-3.5-flash": 0.60,
        "gemini-2.5-flash": 0.99,
        "gemini-3.1-flash-lite": 0.99,
    }

    async def mock_rs_post(url, *args, **kwargs):
        for mid, p in probs.items():
            if mid in url:
                return MockHTTPXResponse({"prob_true": p, "decision_id": 100})
        return MockHTTPXResponse({"prob_true": 0.0, "decision_id": 0})

    with patch("httpx.AsyncClient.post", side_effect=mock_rs_post):
        request = RoutingRequest(
            query="Test flagship tier constraint",
            parameters={"model": "tier:flagship"},
        )
        ranked = await base_router.get_ranked_models(request, strategy="expected_utility")
        ranked_ids = [m.model_id for m in ranked]

        assert "gemini-3.5-flash" in ranked_ids
        assert "gemini-2.5-flash" not in ranked_ids
        assert "gemini-3.1-flash-lite" not in ranked_ids

        request2 = RoutingRequest(
            query="Test flagship tier constraint routed",
            parameters={"model": "tier:flagship"},
        )
        selected = await base_router.route_request(request2, strategy="expected_utility")
        assert selected.model_id == "gemini-3.5-flash"


@pytest.mark.asyncio
async def test_explicit_model_pinning_routes_only_pinned_model(base_router):
    """3. Exact model pinning routes only to that model regardless of others."""
    probs = {
        "gemini-3.5-flash": 0.99,
        "gemini-2.5-flash": 0.50,
        "gemini-3.1-flash-lite": 0.50,
    }

    async def mock_rs_post(url, *args, **kwargs):
        for mid, p in probs.items():
            if mid in url:
                return MockHTTPXResponse({"prob_true": p, "decision_id": 100})
        return MockHTTPXResponse({"prob_true": 0.0, "decision_id": 0})

    with patch("httpx.AsyncClient.post", side_effect=mock_rs_post):
        request = RoutingRequest(
            query="Test pinned model routing",
            parameters={"model": "gemini-2.5-flash"},
        )
        ranked = await base_router.get_ranked_models(request, strategy="expected_utility")
        assert len(ranked) == 1
        assert ranked[0].model_id == "gemini-2.5-flash"

        request2 = RoutingRequest(
            query="Test pinned model routing result",
            parameters={"model": "gemini-2.5-flash"},
        )
        selected = await base_router.route_request(request2, strategy="expected_utility")
        assert selected.model_id == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_disabled_pinned_model_is_rejected_without_fallback(base_router):
    """4. Disabled pinned model is rejected with ValueError (no silent fallback).

    Validates that when a requested model is disabled via settings.disabled_models,
    get_ranked_models returns [] and route_request raises ValueError. No silent
    fallback to other models should occur.
    """
    settings = get_settings()
    original_disabled = list(settings.disabled_models)

    try:
        # Set the pinned model as disabled
        settings.disabled_models = ["gemini-2.5-flash"]

        request = RoutingRequest(
            query="Test disabled pinned rejection",
            parameters={"model": "gemini-2.5-flash"},
        )

        # 1) Verify get_ranked_models filters out the disabled model entirely
        ranked = await base_router.get_ranked_models(request, strategy="expected_utility")
        assert ranked == [], (
            "get_ranked_models should return empty list for a pinned but disabled model"
        )

        # 2) Verify route_request raises ValueError instead of silently routing
        #    or returning HTTP 500 / None.
        with pytest.raises((ValueError, HTTPException)) as excinfo:
            await base_router.route_request(request, strategy="expected_utility")

        error_text = str(excinfo.value).lower()
        assert any(
            term in error_text
            for term in [
                "no eligible models",
                "no eligible model",
                "unavailable",
                "disabled",
                "all models failed",
            ]
        ), (
            f"Expected a clear 'no eligible' / 'unavailable' / 'disabled' error, "
            f"got: {excinfo.value!r}"
        )
    finally:
        # Restore original settings so subsequent tests are unaffected
        settings.disabled_models = original_disabled
