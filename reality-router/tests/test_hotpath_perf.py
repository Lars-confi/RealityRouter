"""Tests for hot-path performance optimizations."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.models.routing import RoutingRequest
from src.router.agent_detector import TrajectoryTracker
from src.router.core import RouterCore


def _make_bare_core(sentiment_adapter):
    core = RouterCore.__new__(RouterCore)
    core.models = {}
    core.adapters = {"sentiment-model": sentiment_adapter}
    core.translated_languages = set(["english"])
    core._language_check_in_flight = False
    core.trajectory_tracker = TrajectoryTracker()
    core.active_sessions = {}
    core.concurrency_limits = {}
    return core


def test_needs_language_detection_heuristics():
    core = _make_bare_core(None)
    # Short / empty / English / code -> False
    assert not core._needs_language_detection(None)
    assert not core._needs_language_detection("")
    assert not core._needs_language_detection("Hi")
    assert not core._needs_language_detection("hello")
    assert not core._needs_language_detection("Fix the bug in main.py")
    assert not core._needs_language_detection("This is a normal English text with no special characters.")
    assert not core._needs_language_detection("def hello():\n    return \"world\"")
    assert not core._needs_language_detection("Check https://example.com for details")
    assert not core._needs_language_detection("uno dos tres")  # < 4 ASCII words

    # Non-ASCII scripts, accented Latin, and unaccented non-English prose -> True
    assert core._needs_language_detection("こんにちは、世界！テストです")
    assert core._needs_language_detection("München ist eine schöne Stadt.")
    assert core._needs_language_detection("Café en España hoy")
    assert core._needs_language_detection("Por favor explica como ordenar una lista rapidamente")


def test_route_request_gates_background_language_detection():
    class _StopAfterGate(Exception):
        pass

    async def _run():
        fake_settings = SimpleNamespace(
            default_strategy="expected_utility",
            sentiment_model_id="sentiment-model",
            enable_sticky_sessions=False,
            agent_handling="auto",
        )
        fake_db = SimpleNamespace(close=lambda: None)

        # 1. English prompt -> 0 LLM calls to sentiment adapter
        adapter_en = SimpleNamespace(forward_request=AsyncMock(return_value={"text": "English"}))
        core_en = _make_bare_core(adapter_en)
        with (
            patch("src.router.core.get_settings", return_value=fake_settings),
            patch("src.router.core.SessionLocal", return_value=fake_db),
            patch("src.router.core.keyword_manager.translate_and_add_keywords", new=AsyncMock(return_value=True)) as mock_trans,
            patch.object(core_en, "get_ranked_models", side_effect=_StopAfterGate()),
        ):
            try:
                await core_en.route_request(RoutingRequest(query="Fix the bug in main.py"))
            except _StopAfterGate:
                pass
            await asyncio.sleep(0)
            assert adapter_en.forward_request.await_count == 0
            assert mock_trans.await_count == 0
            assert core_en._language_check_in_flight is False

        # 2. Non-ASCII prompt -> 1 detection call + 1 translate call, and flag resets
        adapter_ja = SimpleNamespace(forward_request=AsyncMock(return_value={"text": "Japanese"}))
        core_ja = _make_bare_core(adapter_ja)
        with (
            patch("src.router.core.get_settings", return_value=fake_settings),
            patch("src.router.core.SessionLocal", return_value=fake_db),
            patch("src.router.core.keyword_manager.translate_and_add_keywords", new=AsyncMock(return_value=True)) as mock_trans,
            patch.object(core_ja, "get_ranked_models", side_effect=_StopAfterGate()),
        ):
            try:
                await core_ja.route_request(RoutingRequest(query="こんにちは、世界！テストです"))
            except _StopAfterGate:
                pass
            await asyncio.sleep(0)
            assert adapter_ja.forward_request.await_count == 1
            assert mock_trans.await_count == 1
            assert "japanese" in core_ja.translated_languages
            assert core_ja._language_check_in_flight is False

        # 3. Overlapping in-flight check -> skipped while _language_check_in_flight is True
        adapter_busy = SimpleNamespace(forward_request=AsyncMock(return_value={"text": "German"}))
        core_busy = _make_bare_core(adapter_busy)
        core_busy._language_check_in_flight = True
        with (
            patch("src.router.core.get_settings", return_value=fake_settings),
            patch("src.router.core.SessionLocal", return_value=fake_db),
            patch("src.router.core.keyword_manager.translate_and_add_keywords", new=AsyncMock(return_value=True)) as mock_trans,
            patch.object(core_busy, "get_ranked_models", side_effect=_StopAfterGate()),
        ):
            try:
                await core_busy.route_request(RoutingRequest(query="München ist eine schöne Stadt."))
            except _StopAfterGate:
                pass
            await asyncio.sleep(0)
            assert adapter_busy.forward_request.await_count == 0
            assert mock_trans.await_count == 0
            assert core_busy._language_check_in_flight is True

    asyncio.run(_run())
