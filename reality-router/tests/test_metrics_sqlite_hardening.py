"""Regression tests for SQLite concurrency pragmas/indexes and scalar-projected /metrics/summary."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from src.models.database import RoutingLog, configure_sqlite_engine, init_db
from src.router.metrics import get_metrics_summary


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

        # Second init_db call must remain idempotent
        init_db(eng)
    finally:
        eng.dispose()


def test_metrics_summary_projects_scalar_columns_without_loading_payload_blobs(tmp_path):
    """get_metrics_summary aggregates accurately without selecting large payload/text/features blobs."""
    eng, session_factory = _make_temp_db(tmp_path, "projection.db")
    huge_blob = "X" * 10000
    now = datetime.utcnow()
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
