"""Regression tests for SQLite concurrency pragmas/indexes and scalar-projected /metrics/summary."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from src.models.database import RoutingLog, configure_sqlite_engine, init_db, utcnow
from src.router.metrics import get_metrics_history, get_metrics_summary


def _make_temp_db(tmp_path, name="hardening.db"):
    db_file = tmp_path / name
    eng = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    configure_sqlite_engine(eng)
    init_db(eng)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    return eng, session_factory


def test_sqlite_wal_busy_timeout_and_indexes_initialized(tmp_path):
    """init_db + configure_sqlite_engine enables WAL, busy_timeout>=5000, and timestamp/failure indexes."""
    eng, _ = _make_temp_db(tmp_path)
    try:
        with eng.connect() as conn:
            journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
            synchronous = conn.execute(text("PRAGMA synchronous")).scalar()
            indexes = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='routing_logs'")
                ).fetchall()
            }

        assert str(journal_mode).lower() == "wal"
        assert int(busy_timeout) >= 5000
        assert int(synchronous) == 1
        assert "ix_routing_logs_timestamp" in indexes
        assert "ix_routing_logs_success_timestamp" in indexes
        assert "ix_routing_logs_model_timestamp" in indexes
        assert "ix_routing_logs_agent_timestamp" in indexes

        # Second init_db call must remain idempotent
        init_db(eng)
    finally:
        eng.dispose()


def test_metrics_summary_projects_scalar_columns_without_loading_payload_blobs(tmp_path):
    """get_metrics_summary aggregates accurately without selecting large payload/text/features blobs."""
    eng, session_factory = _make_temp_db(tmp_path, "projection.db")
    huge_blob = "X" * 10000
    now = utcnow()
    try:
        db = session_factory()
        try:
            db.add_all(
                [
                    RoutingLog(
                        timestamp=now - timedelta(minutes=2),
                        query=huge_blob,
                        agent_id="agent-alpha",
                        model_id="m-good",
                        model_name="Model Good",
                        expected_utility=85.0,
                        cost=0.01,
                        potential_cost=0.05,
                        time=1.2,
                        probability=0.92,
                        success=True,
                        response_text=huge_blob,
                        prompt_tokens=100,
                        completion_tokens=50,
                        total_tokens=150,
                        request_payload=huge_blob,
                        response_payload=huge_blob,
                        routing_context=json.dumps([{"model_id": "m-good", "utility": 85.0}]),
                        features_json=huge_blob,
                        user_sentiment="happy",
                        strategy="expected_utility",
                    ),
                    RoutingLog(
                        timestamp=now - timedelta(minutes=1),
                        query=huge_blob,
                        agent_id="agent-alpha",
                        model_id="m-bad",
                        model_name="Model Bad",
                        expected_utility=40.0,
                        cost=0.02,
                        potential_cost=0.02,
                        time=0.5,
                        probability=0.55,
                        success=False,
                        response_text=huge_blob,
                        prompt_tokens=20,
                        completion_tokens=0,
                        total_tokens=20,
                        request_payload=huge_blob,
                        response_payload="HTTP 401 Unauthorized: invalid api key",
                        routing_context=json.dumps([]),
                        features_json=huge_blob,
                        user_sentiment="unhappy",
                        strategy="expected_utility",
                    ),
                ]
            )
            db.commit()

            executed_sql = []

            @event.listens_for(eng, "before_cursor_execute")
            def _capture_sql(_conn, _cursor, statement, _params, _context, _executemany):
                executed_sql.append(statement)

            summary = asyncio.run(get_metrics_summary(window="all", db=db))
        finally:
            db.close()

        assert len(executed_sql) >= 3
        agg_sql = executed_sql[0].lower()
        for forbidden_col in (
            "request_payload",
            "response_payload",
            "response_text",
            "features_json",
            "first_token_top_logprobs",
            "second_token_top_logprobs",
        ):
            assert forbidden_col not in agg_sql, f"Aggregation SQL unexpectedly selected {forbidden_col}: {agg_sql}"

        assert summary.total_requests == 2
        assert abs(summary.average_utility - 62.5) < 1e-9
        assert abs(summary.total_cost - 0.03) < 1e-9
        assert abs(summary.potential_max_cost - 0.07) < 1e-9
        assert summary.total_tokens == 170
        assert summary.models["m-good"]["positive_feedback_count"] == 1
        assert summary.models["m-good"]["feedback_count"] == 1
        assert summary.models["m-bad"]["positive_feedback_count"] == 0
        assert summary.models["m-bad"]["feedback_count"] == 1
        assert len(summary.recent_events) == 2
        assert summary.api_key_alerts is not None
        assert len(summary.api_key_alerts) == 1
        assert summary.api_key_alerts[0]["model_id"] == "m-bad"
        assert summary.api_key_alerts[0]["category"] == "credentials"
    finally:
        eng.dispose()


def test_sqlite_concurrent_writes_and_summary_reads(tmp_path):
    """WAL + busy_timeout allows concurrent RoutingLog writes and /metrics/summary reads without lock errors."""
    eng, session_factory = _make_temp_db(tmp_path, "concurrent.db")
    writes_per_worker = 15
    num_writers = 3

    def _writer(worker_idx: int) -> int:
        for i in range(writes_per_worker):
            db = session_factory()
            try:
                db.add(
                    RoutingLog(
                        query=f"q-{worker_idx}-{i}",
                        agent_id=f"agent-{worker_idx}",
                        model_id="m-concurrent",
                        model_name="Model Concurrent",
                        expected_utility=75.0,
                        cost=0.001,
                        potential_cost=0.002,
                        time=0.25,
                        probability=0.9,
                        success=True,
                        prompt_tokens=10,
                        completion_tokens=10,
                        total_tokens=20,
                        user_sentiment="happy" if i % 2 == 0 else None,
                    )
                )
                db.commit()
            finally:
                db.close()
        return writes_per_worker

    def _reader() -> int:
        reads = 0
        for _ in range(15):
            db = session_factory()
            try:
                summary = asyncio.run(get_metrics_summary(window="all", db=db))
                assert summary.total_requests >= 0
                reads += 1
            finally:
                db.close()
        return reads

    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            writer_futs = [pool.submit(_writer, idx) for idx in range(num_writers)]
            reader_futs = [pool.submit(_reader) for _ in range(2)]
            assert sum(f.result() for f in writer_futs) == num_writers * writes_per_worker
            assert sum(f.result() for f in reader_futs) == 30

        db = session_factory()
        try:
            final_summary = asyncio.run(get_metrics_summary(window="all", db=db))
            assert final_summary.total_requests == num_writers * writes_per_worker
        finally:
            db.close()
    finally:
        eng.dispose()


def test_load_balancer_performance_selection_projects_scalar_columns(tmp_path):
    """LoadBalancer.get_next_model_performance_based selects only (success, time) without blobs."""
    from src.router.load_balancer import LoadBalancer

    eng, session_factory = _make_temp_db(tmp_path, "lb_proj.db")
    huge_blob = "Y" * 10000
    try:
        db = session_factory()
        try:
            db.add_all(
                [
                    RoutingLog(
                        query=huge_blob,
                        model_id="m-fast",
                        model_name="Fast",
                        expected_utility=80.0,
                        cost=0.001,
                        time=0.2,
                        probability=0.9,
                        success=True,
                        request_payload=huge_blob,
                        response_payload=huge_blob,
                        features_json=huge_blob,
                    ),
                    RoutingLog(
                        query=huge_blob,
                        model_id="m-slow",
                        model_name="Slow",
                        expected_utility=80.0,
                        cost=0.001,
                        time=4.0,
                        probability=0.9,
                        success=True,
                        request_payload=huge_blob,
                        response_payload=huge_blob,
                        features_json=huge_blob,
                    ),
                ]
            )
            db.commit()

            lb = LoadBalancer()
            lb.add_model("m-fast", "Fast", weight=1.0)
            lb.add_model("m-slow", "Slow", weight=1.0)

            executed_sql = []

            @event.listens_for(eng, "before_cursor_execute")
            def _capture(_conn, _cursor, statement, _params, _context, _executemany):
                executed_sql.append(statement)

            chosen = lb.get_next_model_performance_based(db)
        finally:
            db.close()

        assert chosen == "m-fast"
        assert len(executed_sql) >= 2
        for stmt in executed_sql:
            low = stmt.lower()
            for col in ("request_payload", "response_payload", "response_text", "features_json", "routing_context"):
                assert col not in low, f"LoadBalancer SQL unexpectedly selected {col}: {stmt}"
    finally:
        eng.dispose()


def test_router_core_recent_stats_query_projects_scalar_columns(tmp_path, monkeypatch):
    """RouterCore.get_ranked_models projects only (time, completion_tokens) when reading model history."""
    from unittest.mock import AsyncMock, MagicMock

    import src.router.core as core_mod
    from src.models.routing import RoutingRequest

    eng, session_factory = _make_temp_db(tmp_path, "core_proj.db")
    huge_blob = "Z" * 10000
    try:
        db = session_factory()
        try:
            db.add(
                RoutingLog(
                    query=huge_blob,
                    model_id="m-local",
                    model_name="Local Model",
                    expected_utility=70.0,
                    cost=0.0,
                    time=1.25,
                    probability=0.9,
                    success=True,
                    completion_tokens=200,
                    request_payload=huge_blob,
                    response_payload=huge_blob,
                    response_text=huge_blob,
                    features_json=huge_blob,
                )
            )
            db.commit()
        finally:
            db.close()

        monkeypatch.setattr(core_mod, "SessionLocal", session_factory)
        router = core_mod.RouterCore.__new__(core_mod.RouterCore)
        router.models = {
            "m-local": {
                "name": "Local Model",
                "cost": 0.0,
                "prompt_cost": 0.0,
                "completion_cost": 0.0,
                "time": 10.0,
                "probability": 0.8,
                "provider": "ollama",
                "supports_function_calling": True,
                "is_local": True,
            }
        }
        router.load_balancer = MagicMock()
        router.load_balancer.is_model_healthy.return_value = True
        router.utility_calculator = core_mod.ExpectedUtilityCalculator(cost_sensitivity=0.5, time_sensitivity=0.5)
        router.extract_coding_features = MagicMock(return_value={"model_id": "m-local"})

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"prob_true": 0.9, "uncertainty": 0.05, "decision_id": "dec-1"}
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)
        monkeypatch.setattr(core_mod.httpx, "AsyncClient", lambda **kwargs: mock_client)

        executed_sql = []

        @event.listens_for(eng, "before_cursor_execute")
        def _capture(_conn, _cursor, statement, _params, _context, _executemany):
            executed_sql.append(statement)

        req = RoutingRequest(query="hello", strategy="expected_utility", parameters={"messages": [{"role": "user", "content": "hello"}]})
        ranked = asyncio.run(router.get_ranked_models(req, strategy="expected_utility"))

        assert len(ranked) == 1
        assert ranked[0].model_id == "m-local"
        assert abs(ranked[0].time - 1.25) < 1e-6
        assert len(executed_sql) >= 1
        for stmt in executed_sql:
            low = stmt.lower()
            for col in ("request_payload", "response_payload", "response_text", "features_json", "routing_context"):
                assert col not in low, f"RouterCore SQL unexpectedly selected {col}: {stmt}"
    finally:
        eng.dispose()


def test_metrics_history_returns_bounded_scalar_rows_from_db(tmp_path):
    """GET /metrics/history returns newest-first scalar rows from routing_logs without payload blobs."""
    eng, session_factory = _make_temp_db(tmp_path, "history.db")
    huge_blob = "H" * 10000
    base_ts = utcnow() - timedelta(minutes=10)
    try:
        db = session_factory()
        try:
            assert asyncio.run(get_metrics_history(limit=10, db=db)) == []

            for i in range(5):
                db.add(
                    RoutingLog(
                        timestamp=base_ts + timedelta(minutes=i),
                        query=huge_blob,
                        agent_id="agent-hist",
                        model_id=f"m-{i}",
                        model_name=f"Model {i}",
                        expected_utility=50.0 + i * 10.0,
                        cost=0.01 * (i + 1),
                        potential_cost=0.05,
                        time=0.5 + i * 0.1,
                        probability=0.85,
                        success=True,
                        response_text=huge_blob,
                        prompt_tokens=10,
                        completion_tokens=20,
                        total_tokens=30,
                        request_payload=huge_blob,
                        response_payload=huge_blob,
                        routing_context=huge_blob,
                        features_json=huge_blob,
                        strategy="expected_utility",
                        user_sentiment="happy" if i % 2 == 0 else None,
                    )
                )
            db.commit()

            executed_sql = []

            @event.listens_for(eng, "before_cursor_execute")
            def _capture(_conn, _cursor, statement, _params, _context, _executemany):
                executed_sql.append(statement)

            rows = asyncio.run(get_metrics_history(limit=3, db=db))
            clamped_min = asyncio.run(get_metrics_history(limit=0, db=db))
        finally:
            db.close()

        assert len(rows) == 3
        assert [r["model_id"] for r in rows] == ["m-4", "m-3", "m-2"]
        assert rows[0]["expected_utility"] == 90.0
        assert rows[0]["agent_id"] == "agent-hist"
        assert rows[0]["user_sentiment"] == "happy"
        assert len(clamped_min) == 1
        assert clamped_min[0]["model_id"] == "m-4"

        assert len(executed_sql) >= 1
        low_sql = executed_sql[0].lower()
        for forbidden_col in ("request_payload", "response_payload", "response_text", "features_json", "routing_context"):
            assert forbidden_col not in low_sql, f"History SQL unexpectedly selected {forbidden_col}: {executed_sql[0]}"
    finally:
        eng.dispose()
