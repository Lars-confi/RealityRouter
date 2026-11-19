import os
import json
import sys
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from src.config.settings import get_settings, reload_settings
from src.main import app
from src.router.core import router_core
from tests.conftest import MockHTTPXResponse

PARENT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PARENT_ROOT not in sys.path:
    sys.path.insert(0, PARENT_ROOT)
import start_router

def test_metrics_and_control_plane_contracts():
    router_core.models = {
        "gemini-2.5-flash": {
            "name": "Gemini 2.5 Flash",
            "cost": 0.001,
            "time": 1.0,
            "probability": 0.8,
            "supports_function_calling": True,
        }
    }
    router_core.all_discovered_models = [
        {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "provider": "gemini", "enabled": True}
    ]

    s_get = client.get("/metrics/strategy")
    assert s_get.status_code == 200
    assert "strategy" in s_get.json()
    s_post = client.post("/metrics/strategy", json={"strategy": "tiered_assessment"})
    assert s_post.status_code == 200
    assert client.get("/metrics/strategy").json()["strategy"] == "tiered_assessment"
    client.post("/metrics/strategy", json={"strategy": "expected_utility"})
    assert client.get("/metrics/strategy").json()["strategy"] == "expected_utility"

    p_get = client.get("/metrics/preferences")
    assert p_get.status_code == 200
    assert "value" in p_get.json()
    p_post = client.post("/metrics/preferences", json={"value": 75})
    assert p_post.status_code == 200
    assert client.get("/metrics/preferences").json()["value"] == 75

    m_get = client.get("/metrics/models")
    assert m_get.status_code == 200
    assert isinstance(m_get.json(), list)

    ma_get = client.get("/metrics/models/all")
    assert ma_get.status_code == 200
    assert "models" in ma_get.json()

    h_get = client.get("/metrics/history")
    assert h_get.status_code == 200
    assert isinstance(h_get.json(), list)

def test_model_activation_single_source_of_truth():
    home = os.environ["REALITY_ROUTER_HOME"]
    env_file = os.path.join(home, ".env")
    stale_disabled_file = os.path.join(home, "disabled_models.json")
    with open(stale_disabled_file, "w") as f:
        json.dump(["model-a"], f)

    with patch.object(start_router, "ENV_FILE", env_file), \
         patch.object(start_router, "DISABLED_MODELS_FILE", stale_disabled_file), \
         patch("src.config.settings._env_path", env_file), \
         patch("src.config.settings.APP_HOME", home):
        env_vars = start_router.load_env()
        start_router.save_disabled_models(
            disabled_set={"model-b"},
            env_vars=env_vars,
            enabled_set={"model-a"},
        )
        assert not os.path.exists(stale_disabled_file)

        settings = reload_settings()
        assert settings.is_model_enabled("model-a") is True
        assert settings.is_model_enabled("model-b") is False
        assert settings.is_model_enabled("model-c-unactivated") is False

        mock_cfg_models = {
            "model-a": {"name": "Model A"},
            "model-b": {"name": "Model B"},
            "model-c-unactivated": {"name": "Model C"},
        }
        with patch("src.router.core.load_models_from_config", return_value=mock_cfg_models):
            router_core.reload()
            ma_response = client.get("/metrics/models/all")
            assert ma_response.status_code == 200
            by_id = {m["id"]: m for m in ma_response.json()["models"]}
            assert by_id["model-a"]["status_category"] == "active"
            assert by_id["model-a"]["preference"] == 100.0
            assert by_id["model-b"]["status_category"] == "disabled"
            assert by_id["model-b"]["preference"] == 0.0
            assert by_id["model-c-unactivated"]["status_category"] == "disabled"
            assert by_id["model-c-unactivated"]["preference"] == 0.0

            env_vars2 = start_router.load_env()
            assert start_router.is_model_enabled_in_config("model-a", env_vars2) is True
            assert start_router.is_model_enabled_in_config("model-b", env_vars2) is False
            assert start_router.is_model_enabled_in_config("model-c-unactivated", env_vars2) is False

            client.post("/metrics/models/preference", json={"model_id": "model-c-unactivated", "value": 80.0})
            client.post("/metrics/models/preference", json={"model_id": "model-a", "value": 0.0})

            env_vars3 = start_router.load_env()
            assert get_settings().is_model_enabled("model-c-unactivated") is True
            assert get_settings().is_model_enabled("model-a") is False
            assert start_router.is_model_enabled_in_config("model-c-unactivated", env_vars3) is True
            assert start_router.is_model_enabled_in_config("model-a", env_vars3) is False

            ma_response2 = client.get("/metrics/models/all")
            by_id2 = {m["id"]: m for m in ma_response2.json()["models"]}
            assert by_id2["model-a"]["status_category"] == "disabled"
            assert by_id2["model-a"]["preference"] == 0.0
            assert by_id2["model-c-unactivated"]["status_category"] == "active"
            assert by_id2["model-c-unactivated"]["preference"] == 80.0
