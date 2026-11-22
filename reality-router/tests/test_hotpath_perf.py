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


def test_extract_coding_features_with_base_features_matches_direct_call():
    from src.adapters.litellm_adapter import LiteLLMAdapter
    core = RouterCore.__new__(RouterCore)
    core.models = {
        "local-qwen": {"name": "Local Qwen", "cost": 0.0, "prompt_cost": 0.0, "completion_cost": 0.0, "supports_function_calling": True, "supports_logprobs": True, "max_input_tokens": 32768, "max_tokens": 4096},
        "cloud-gpt": {"name": "Cloud GPT", "cost": 0.002, "prompt_cost": 0.001, "completion_cost": 0.003, "supports_function_calling": True, "supports_logprobs": False, "max_input_tokens": 128000, "max_tokens": 8192},
    }
    core.adapters = {
        "local-qwen": LiteLLMAdapter("ollama/qwen3-coder:30b", base_url="http://127.0.0.1:11434/v1"),
        "cloud-gpt": LiteLLMAdapter("openai/gpt-4o", api_key="sk-dummy"),
    }
    req = RoutingRequest(query="def add(a, b):\n    return a + b\n", agent_id="perf-test", parameters={"messages": [{"role": "user", "content": "Fix the error and write tests"}]})
    base = core._extract_request_features(req)
    for mid in ("local-qwen", "cloud-gpt"):
        assert core.extract_coding_features(req, mid, base_features=base) == core.extract_coding_features(req, mid)


def test_get_ranked_models_batches_routing_log_stats_into_single_query(tmp_path, monkeypatch):
    from datetime import timedelta
    from unittest.mock import MagicMock
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    import src.router.core as core_mod
    from src.models.database import RoutingLog, configure_sqlite_engine, init_db, utcnow

    eng = create_engine(f"sqlite:///{tmp_path / 'batched.db'}", connect_args={"check_same_thread": False})
    configure_sqlite_engine(eng)
    init_db(eng)
    sf = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    blob, base_ts = "B" * 8000, utcnow() - timedelta(minutes=60)
    try:
        db = sf()
        try:
            for mid, recent_t in (("m-a", 1.0), ("m-b", 2.0), ("m-c", 3.0)):
                for i in range(25):
                    db.add(RoutingLog(timestamp=base_ts + timedelta(seconds=i), query=blob, model_id=mid, model_name=mid, expected_utility=50.0, cost=0.0, time=100.0 if i < 5 else recent_t, probability=0.8, success=True, completion_tokens=100, request_payload=blob, response_payload=blob, response_text=blob, features_json=blob))
            db.commit()
        finally:
            db.close()
        monkeypatch.setattr(core_mod, "SessionLocal", sf)
        router = core_mod.RouterCore.__new__(core_mod.RouterCore)
        router.models = {m: {"name": m, "cost": 0.0, "prompt_cost": 0.0, "completion_cost": 0.0, "time": 9.0, "probability": 0.8, "supports_function_calling": True} for m in ("m-a", "m-b", "m-c")}
        router.adapters = {}
        router.load_balancer = MagicMock()
        router.load_balancer.is_model_healthy.return_value = True
        router.utility_calculator = core_mod.ExpectedUtilityCalculator(cost_sensitivity=0.5, time_sensitivity=0.5)
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"prob_true": 0.85, "uncertainty": 0.05, "decision_id": "dec-batch"}
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)
        monkeypatch.setattr(core_mod.httpx, "AsyncClient", lambda **kw: mock_client)
        executed_sql, extract_calls = [], []
        event.listen(eng, "before_cursor_execute", lambda _c, _cur, stmt, *_a: executed_sql.append(stmt) if "routing_logs" in stmt.lower() else None)
        orig_extract = router._extract_request_features
        monkeypatch.setattr(router, "_extract_request_features", lambda req, response=None: (extract_calls.append(1), orig_extract(req, response=response))[1])
        ranked = asyncio.run(router.get_ranked_models(RoutingRequest(query="Optimize SQLite query batching", parameters={"messages": [{"role": "user", "content": "Optimize SQLite query batching"}]}), strategy="expected_utility"))
        assert len(ranked) == 3 and len(extract_calls) == 1 and len(executed_sql) == 1
        assert not any(c in executed_sql[0].lower() for c in ("request_payload", "response_payload", "response_text", "features_json", "routing_context"))
        times = {d.model_id: d.time for d in ranked}
        assert abs(times["m-a"] - 1.0) < 1e-6 and abs(times["m-b"] - 2.0) < 1e-6 and abs(times["m-c"] - 3.0) < 1e-6
    finally:
        eng.dispose()
