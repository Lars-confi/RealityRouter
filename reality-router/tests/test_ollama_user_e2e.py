"""Zero-cost end-to-end user-behavior & dashboard UI tests using local Ollama only.

Exercises real CLI (`start_router.py models`), a live isolated dev RealityRouter
server on 127.0.0.1:18080, local RealitySignal fixtures (18081 SNAP / 18082 LADDER),
and local Ollama (127.0.0.1:11434) with all cloud provider API keys stripped.
Never touches production ~/.reality_router or port 8000.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

TESTS_DIR = Path(__file__).resolve().parent
REALITY_ROUTER_DIR = TESTS_DIR.parent if (TESTS_DIR.parent / "src").exists() else Path("/srv/buzz-projects/RealityRouter/reality-router")
REPO_ROOT = REALITY_ROUTER_DIR.parent
START_ROUTER_PY = REPO_ROOT / "start_router.py"

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_TAGS_URL = f"{OLLAMA_BASE_URL}/api/tags"
DEV_ROUTER_HOST = "127.0.0.1"
DEV_ROUTER_PORT = 18080
DEV_ROUTER_URL = f"http://{DEV_ROUTER_HOST}:{DEV_ROUTER_PORT}"
SNAP_URL = "http://127.0.0.1:18081"
LADDER_URL = "http://127.0.0.1:18082"

CLOUD_API_KEY_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "ZAI_API_KEY",
    "XAI_API_KEY",
    "DASHSCOPE_API_KEY",
    "OPENROUTER_API_KEY",
    "LM_STUDIO_BASE_URL",
    "LM_STUDIO_API_KEY",
)


def _discover_local_ollama_models():
    """Return unique local Ollama models sorted smallest-first, excluding llamacpp hashes."""
    if os.environ.get("RR_SKIP_OLLAMA_E2E") == "1":
        pytest.skip("RR_SKIP_OLLAMA_E2E=1")

    try:
        resp = httpx.get(OLLAMA_TAGS_URL, timeout=3.0)
        resp.raise_for_status()
        raw_models = resp.json().get("models", [])
    except Exception as exc:
        pytest.skip(f"Local Ollama unavailable at {OLLAMA_TAGS_URL}: {exc}")

    unique_by_name = {}
    for item in raw_models:
        name = item.get("name")
        if not name or name.startswith("llamacpp:"):
            continue
        size = item.get("size") or 0
        if name not in unique_by_name or size < unique_by_name[name]:
            unique_by_name[name] = size

    sorted_models = sorted(unique_by_name.keys(), key=lambda n: (unique_by_name[n], n))
    if len(sorted_models) < 2:
        pytest.skip(f"Need >=2 non-llamacpp local Ollama models, found {sorted_models}")

    for url in (f"{SNAP_URL}/health", f"{LADDER_URL}/health"):
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code != 200 or r.json().get("status") != "healthy":
                pytest.skip(f"Local RealitySignal unhealthy at {url}")
        except Exception as exc:
            pytest.skip(f"Local RealitySignal unavailable at {url}: {exc}")

    return sorted_models


def _build_isolated_env(home_dir: Path) -> dict:
    """Create a subprocess environment strictly isolated to local Ollama ($0 cloud cost)."""
    env = os.environ.copy()
    for key in CLOUD_API_KEY_VARS:
        env.pop(key, None)
    env["REALITY_ROUTER_HOME"] = str(home_dir)
    env["HOME"] = str(home_dir)
    env["PYTHONPATH"] = str(REALITY_ROUTER_DIR)
    return env


def _write_ollama_env(home_dir: Path, enabled_models: list[str], strategy: str = "expected_utility") -> Path:
    home_dir.mkdir(parents=True, exist_ok=True)
    (home_dir / "config").mkdir(exist_ok=True)
    (home_dir / "logs").mkdir(exist_ok=True)
    env_path = home_dir / ".env"
    prefs = {m: 100.0 for m in enabled_models}
    lines = [
        "DEBUG=True",
        f"CUSTOM_LLM_BASE_URL={OLLAMA_BASE_URL}/v1",
        "CUSTOM_LLM_API_KEY=dummy",
        f"REALITY_ROUTING_URL={SNAP_URL}",
        f"REALITY_REROUTING_URL={LADDER_URL}",
        "REALITY_CHECK_TOKEN=local_unauthenticated",
        "REALITY_CHECK_PROVIDER=Enterprise",
        f"DEFAULT_STRATEGY={strategy}",
        f"ENABLED_MODELS={','.join(enabled_models)}",
        "DISABLED_MODELS=",
        f"MODEL_PREFERENCES={json.dumps(prefs)}",
        "SENTIMENT_MODEL_ID=",
    ]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env_path


def _run_cli_models(home_dir: Path, *extra_args: str) -> list[dict]:
    cmd = [
        sys.executable,
        str(START_ROUTER_PY),
        "models",
        "--non-interactive",
        "--json",
        *extra_args,
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=_build_isolated_env(home_dir),
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert proc.returncode == 0, f"CLI failed ({proc.returncode}): stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return json.loads(proc.stdout.strip())


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _start_dev_server(home_dir: Path) -> subprocess.Popen:
    assert not _port_in_use(DEV_ROUTER_HOST, DEV_ROUTER_PORT), (
        f"Dev port {DEV_ROUTER_PORT} already in use before test start"
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.main:app",
            "--host",
            DEV_ROUTER_HOST,
            "--port",
            str(DEV_ROUTER_PORT),
            "--no-access-log",
        ],
        cwd=str(REALITY_ROUTER_DIR),
        env=_build_isolated_env(home_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise AssertionError(f"Dev server exited early ({proc.returncode}):\n{out}")
        try:
            r = httpx.get(f"{DEV_ROUTER_URL}/health", timeout=1.5)
            if r.status_code == 200:
                return proc
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    out = proc.stdout.read() if proc.stdout else ""
    raise AssertionError(f"Timed out waiting for dev server on {DEV_ROUTER_URL}:\n{out}")


def _stop_dev_server(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    if proc.stdout:
        proc.stdout.close()


def test_ollama_cli_discovery_and_toggling_user_flow(tmp_path):
    """Verify real CLI discovery deduplication and model enable/disable/enable-all flows."""
    models = _discover_local_ollama_models()
    model_a, model_b = models[0], models[1]
    _write_ollama_env(tmp_path, enabled_models=[model_a])

    # 1. Initial selective activation: only model_a enabled, no duplicate IDs
    listed = _run_cli_models(tmp_path)
    ids = [m["id"] for m in listed]
    assert len(ids) == len(set(ids)), f"Duplicate Ollama model IDs returned: {ids}"
    assert all(m["provider"] == "ollama" for m in listed)
    enabled_ids = [m["id"] for m in listed if m["enabled"]]
    assert enabled_ids == [model_a]

    # 2. Disable last active model -> 0 enabled (no allowlist collapse)
    after_disable = _run_cli_models(tmp_path, "--disable-model", model_a)
    assert [m["id"] for m in after_disable if m["enabled"]] == []

    # 3. Enable model_b -> ONLY model_b enabled
    after_enable_b = _run_cli_models(tmp_path, "--enable-model", model_b)
    assert [m["id"] for m in after_enable_b if m["enabled"]] == [model_b]

    # 4. Enable all models -> every discovered Ollama model enabled
    after_enable_all = _run_cli_models(tmp_path, "--enable-all-models")
    assert all(m["enabled"] for m in after_enable_all)
    assert {m["id"] for m in after_enable_all} == set(ids)


def test_ollama_live_ui_activation_routing_reset_and_restart(tmp_path):
    """End-to-end user flow against live dev RealityRouter (18080) + local Ollama + RealitySignal."""
    models = _discover_local_ollama_models()
    model_a, model_b = models[0], models[1]
    _write_ollama_env(tmp_path, enabled_models=[model_a])

    proc = None
    try:
        proc = _start_dev_server(tmp_path)
        with httpx.Client(base_url=DEV_ROUTER_URL, timeout=120.0) as http:
            # 1. Dashboard HTML & control-center elements
            dash = http.get("/metrics/dashboard")
            assert dash.status_code == 200
            assert "REALITY ROUTER CONTROL CENTER" in dash.text
            assert "models-table" in dash.text
            assert "pref-slider" in dash.text
            assert "confirmResetDatabase" in dash.text

            # 2. Initial visibility: ONLY model_a active in dashboard & /v1/models
            all_models = http.get("/metrics/models/all").json()["models"]
            assert all(m["provider"] == "ollama" for m in all_models)
            by_id = {m["id"]: m for m in all_models}
            assert by_id[model_a]["status_category"] == "active"
            assert by_id[model_a]["preference"] == 100.0
            for other_id, info in by_id.items():
                if other_id != model_a:
                    assert info["status_category"] == "disabled", f"{other_id} unexpectedly active"
                    assert info["preference"] == 0.0

            v1_ids = [m["id"] for m in http.get("/v1/models").json()["data"]]
            assert "RealityRouter" in v1_ids
            assert model_a in v1_ids
            assert model_b not in v1_ids
            assert http.get("/.well-known/agent-card.json").status_code == 200

            # 3. Live local Ollama routing with ONLY model_a active ($0 cost)
            r1 = http.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "Reply with only OK."}],
                    "max_tokens": 128,
                },
            )
            assert r1.status_code == 200, f"Chat 1 failed: {r1.status_code} {r1.text}"
            assert r1.headers.get("X-RR-Model") == model_a
            assert r1.json().get("choices")
            assert http.get("/metrics/summary").json()["total_requests"] >= 1
            perf_models_1 = [m["model_id"] for m in http.get("/metrics/models").json()]
            assert model_a in perf_models_1

            # 4. UI flow: deactivate last active model (model_a -> 0.0) BEFORE activating model_b
            assert http.post(
                "/metrics/models/preference",
                json={"model_id": model_a, "value": 0.0},
            ).status_code == 200

            all_zero = http.get("/metrics/models/all").json()["models"]
            assert [m["id"] for m in all_zero if m["status_category"] == "active"] == []
            v1_zero = [m["id"] for m in http.get("/v1/models").json()["data"]]
            assert v1_zero == ["RealityRouter"]
            # With 0 active models, auto-routing must refuse rather than invoke a disabled model
            r_zero = http.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "Reply with only OK."}],
                    "max_tokens": 128,
                },
            )
            assert r_zero.status_code >= 400

            # 5. UI flow: activate model_b (80.0) and verify dashboard, /v1/models, and CLI all agree
            assert http.post(
                "/metrics/models/preference",
                json={"model_id": model_b, "value": 80.0},
            ).status_code == 200

            by_id_b = {m["id"]: m for m in http.get("/metrics/models/all").json()["models"]}
            assert by_id_b[model_b]["status_category"] == "active"
            assert by_id_b[model_b]["preference"] == 80.0
            assert by_id_b[model_a]["status_category"] == "disabled"
            assert by_id_b[model_a]["preference"] == 0.0

            v1_b = [m["id"] for m in http.get("/v1/models").json()["data"]]
            assert model_b in v1_b and model_a not in v1_b

            cli_after_ui = _run_cli_models(tmp_path)
            assert [m["id"] for m in cli_after_ui if m["enabled"]] == [model_b]

            # 6. Live local Ollama routing after toggle -> must route to model_b
            r2 = http.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "Reply with only OK."}],
                    "max_tokens": 128,
                },
            )
            assert r2.status_code == 200, f"Chat 2 failed: {r2.status_code} {r2.text}"
            assert r2.headers.get("X-RR-Model") == model_b
            assert r2.json().get("choices")
            assert http.get("/metrics/summary").json()["total_requests"] >= 2
            perf_models_2 = [m["model_id"] for m in http.get("/metrics/models").json()]
            assert model_b in perf_models_2

            # 6b. Live streaming completion with model_b active
            r_stream = http.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "Say hi."}],
                    "max_tokens": 128,
                    "stream": True,
                },
            )
            assert r_stream.status_code == 200
            assert "text/event-stream" in r_stream.headers.get("content-type", "")
            assert model_b in r_stream.text
            assert "data: [DONE]" in r_stream.text

            # 7. Multi-model weighting & sorting (model_b=80.0, model_a=40.0)
            assert http.post(
                "/metrics/models/preference",
                json={"model_id": model_a, "value": 40.0},
            ).status_code == 200
            multi = http.get("/metrics/models/all").json()["models"]
            active_ordered = [m["id"] for m in multi if m["status_category"] == "active"]
            assert active_ordered == [model_b, model_a]
            # Turn model_a back off so only model_b remains active
            assert http.post(
                "/metrics/models/preference",
                json={"model_id": model_a, "value": 0.0},
            ).status_code == 200

            # 8. UI Strategy & Cost/Speed Preference controls + time-windowed summary
            assert http.post(
                "/metrics/strategy", json={"strategy": "tiered_assessment"}
            ).status_code == 200
            assert http.get("/metrics/strategy").json() == {
                "strategy": "tiered_assessment",
                "mode": "ladder",
            }
            for pref_val in (25, 75):
                assert http.post("/metrics/preferences", json={"value": pref_val}).status_code == 200
                assert http.get("/metrics/preferences").json()["value"] == pref_val
            for window in ("24h", "7d", "30d", "all"):
                w_resp = http.get(f"/metrics/summary?window={window}")
                assert w_resp.status_code == 200
                assert w_resp.json()["total_requests"] >= 2
            assert isinstance(http.get("/metrics/models").json(), list)

            # 9. UI Reset Database -> clears metrics/history while keeping .env model selection & strategy
            reset_resp = http.post("/metrics/reset")
            assert reset_resp.status_code == 200
            assert reset_resp.json()["status"] == "success"
            for window in ("24h", "7d", "30d", "all"):
                summary = http.get(f"/metrics/summary?window={window}").json()
                assert summary["total_requests"] == 0
                assert summary["total_cost"] == 0.0
            assert http.get("/metrics/models").json() == []
            assert isinstance(http.get("/metrics/history").json(), list)
            # Config must remain intact after DB reset
            after_reset_by_id = {
                m["id"]: m for m in http.get("/metrics/models/all").json()["models"]
            }
            assert after_reset_by_id[model_b]["status_category"] == "active"
            assert after_reset_by_id[model_b]["preference"] == 80.0
            assert after_reset_by_id[model_a]["status_category"] == "disabled"
            assert http.get("/metrics/strategy").json()["strategy"] == "tiered_assessment"

        # 10. Server restart persistence on 127.0.0.1:18080
        _stop_dev_server(proc)
        proc = None
        proc = _start_dev_server(tmp_path)
        with httpx.Client(base_url=DEV_ROUTER_URL, timeout=30.0) as http:
            restarted_by_id = {
                m["id"]: m for m in http.get("/metrics/models/all").json()["models"]
            }
            assert [
                m_id for m_id, m in restarted_by_id.items() if m["status_category"] == "active"
            ] == [model_b]
            assert restarted_by_id[model_b]["preference"] == 80.0
            assert restarted_by_id[model_a]["status_category"] == "disabled"
            assert http.get("/metrics/strategy").json()["strategy"] == "tiered_assessment"
            assert http.get("/metrics/summary").json()["total_requests"] == 0
            v1_restarted = [m["id"] for m in http.get("/v1/models").json()["data"]]
            assert model_b in v1_restarted and model_a not in v1_restarted
    finally:
        _stop_dev_server(proc)
        assert not _port_in_use(DEV_ROUTER_HOST, DEV_ROUTER_PORT)


def test_ollama_responses_api_and_strategy_switching_live(tmp_path):
    """Verify live SNAP vs LADDER strategy routing and /v1/responses API with local Ollama."""
    models = _discover_local_ollama_models()
    model_a = models[0]
    _write_ollama_env(tmp_path, enabled_models=[model_a], strategy="expected_utility")

    proc = None
    try:
        proc = _start_dev_server(tmp_path)
        with httpx.Client(base_url=DEV_ROUTER_URL, timeout=120.0) as http:
            # 1. SNAP strategy (expected_utility) live chat completion
            assert http.get("/metrics/strategy").json() == {
                "strategy": "expected_utility",
                "mode": "snap",
            }
            r_snap = http.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "Reply with only OK."}],
                    "max_tokens": 128,
                },
            )
            assert r_snap.status_code == 200, f"SNAP chat failed: {r_snap.status_code} {r_snap.text}"
            assert r_snap.headers.get("X-RR-Model") == model_a
            assert r_snap.headers.get("X-RR-Strategy") == "expected_utility"

            # 2. Switch via UI alias "ladder" -> tiered_assessment and route live
            assert http.post("/metrics/strategy", json={"strategy": "ladder"}).status_code == 200
            assert http.get("/metrics/strategy").json() == {
                "strategy": "tiered_assessment",
                "mode": "ladder",
            }
            r_ladder = http.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "Reply with only OK."}],
                    "max_tokens": 128,
                },
            )
            assert r_ladder.status_code == 200, f"LADDER chat failed: {r_ladder.status_code} {r_ladder.text}"
            assert r_ladder.headers.get("X-RR-Model") == model_a
            assert r_ladder.headers.get("X-RR-Strategy") == "tiered_assessment"

            # 3. Switch back via UI alias "snap" -> expected_utility
            assert http.post("/metrics/strategy", json={"strategy": "snap"}).status_code == 200
            assert http.get("/metrics/strategy").json() == {
                "strategy": "expected_utility",
                "mode": "snap",
            }

            # 4. Live OpenAI Responses API (/v1/responses) against local Ollama
            r_resp = http.post(
                "/v1/responses",
                json={
                    "model": "auto",
                    "input": "Reply with only OK.",
                },
            )
            assert r_resp.status_code == 200, f"/v1/responses failed: {r_resp.status_code} {r_resp.text}"
            resp_body = r_resp.json()
            assert resp_body.get("object") == "response" or "output" in resp_body

            # 5. Confirm /health reports exactly 1 active model and /metrics/reset clears all 3 requests
            health = http.get("/health").json()
            assert health["status"] == "healthy"
            assert health["active_models"] == 1
            assert http.get("/metrics/summary").json()["total_requests"] >= 3
            assert http.post("/metrics/reset").status_code == 200
            assert http.get("/metrics/summary").json()["total_requests"] == 0
    finally:
        _stop_dev_server(proc)
        assert not _port_in_use(DEV_ROUTER_HOST, DEV_ROUTER_PORT)
