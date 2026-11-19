import sys, os; sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import os
import json
import pytest
from unittest.mock import patch, MagicMock
from src.config.settings import get_settings, reload_settings
import start_router

def test_allowlist_collapse_bug():
    """
    Verify that setting ENABLED_MODELS="" in .env prevents all models from being enabled,
    instead of collapsing back to 'enable all'.
    """
    home = os.environ.get("REALITY_ROUTER_HOME", "/tmp/rr_test_home")
    os.makedirs(home, exist_ok=True)
    env_path = os.path.join(home, ".env")
    
    # Case 1: ENABLED_MODELS is NOT present -> all should be enabled (default)
    with open(env_path, "w") as f:
        f.write("USER_EMAIL=test@example.com\n")
    
    with patch("src.config.settings._env_path", env_path),          patch("src.config.settings.APP_HOME", home):
        reload_settings()
        s = get_settings()
        # By default, if no allowlist, is_model_enabled should return True for a random model
        assert s.is_model_enabled("some-model") is True, "Default should be enable-all"

    # Case 2: ENABLED_MODELS is present but EMPTY -> none should be enabled
    with open(env_path, "w") as f:
        f.write("ENABLED_MODELS=\n")
    
    with patch("src.config.settings._env_path", env_path),          patch("src.config.settings.APP_HOME", home):
        reload_settings()
        s = get_settings()
        # Now we have an active allowlist, but it's empty.
        assert s.is_model_enabled("some-model") is False, "Empty allowlist should disable all models"

    # Also check the start_router implementation
    env_vars = {"ENABLED_MODELS": ""}
    assert start_router.is_model_enabled_in_config("some-model", env_vars) is False, "start_router logic should also handle empty allowlist"

def test_ollama_deduplication():
    """
    Verify that sync_discover_ollama deduplicates model names.
    """
    mock_response_data = {
        "models": [
            {"name": "llama3:latest", "details": {"family": "llama"}},
            {"name": "llama3:latest", "details": {"family": "llama"}}, # Duplicate
            {"name": "mistral:latest", "details": {"family": "mistral"}},
        ]
    }
    
    with patch("urllib.request.urlopen") as mock_urlopen:
        # Mock the response from Ollama's /api/tags
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_response_data).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp
        
        discovered = start_router.sync_discover_ollama("http://localhost:11434")
        
        # Should only have 2 unique models
        assert len(discovered) == 2
        model_ids = [m["id"] for m in discovered]
        assert "llama3:latest" in model_ids
        assert "mistral:latest" in model_ids
        assert model_ids.count("llama3:latest") == 1, "Llama3 should be deduplicated"

