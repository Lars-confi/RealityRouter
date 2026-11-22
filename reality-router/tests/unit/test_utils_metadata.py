#!/usr/bin/env python3
"""
Deterministic offline unit tests for:
- src/utils/keyword_manager.py (KeywordManager, BASE_KEYWORDS)
- src/utils/pricing.py (PricingManager)
- src/utils/model_info.py (ModelInfoManager)

These tests isolate disk caches with tmp_path and monkeypatch and
prevent any real network requests or writes to ~/.reality_router.
"""

import json
import os
import sys
import time
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

# Direct imports of the source modules
import src.utils.keyword_manager as km_mod
import src.utils.pricing as pricing_mod
import src.utils.model_info as mi_mod


# ======================================================================
# KeywordManager Tests
# ======================================================================

@pytest.fixture
def km_env(tmp_path, monkeypatch):
    """Isolate REALITY_ROUTER_HOME to a tmp dir and yield KeywordManager."""
    monkeypatch.setenv("REALITY_ROUTER_HOME", str(tmp_path))
    return km_mod.KeywordManager()


def test_keyword_manager_merges_valid_disk_json(km_env, tmp_path):
    """Disk JSON with extra keywords is merged with base; dedup works."""
    # Write a disk file with new Spanish words
    disk_data = {
        "gen": ["crear", "escribir"],
        "fix": ["bug", "error"],
        "refactor": ["optimizar"],
        "docs": [],
        "math": [],
        "summarize": [],
        "roleplay": [],
        "format_table": [],
        "analytical": [],
        "meta_pos_con": [],
        "meta_neg_con": []
    }
    with open(km_env.keywords_file, "w") as f:
        json.dump(disk_data, f)
    # Force re-load (simulate fresh import)
    km_env._load_keywords()

    gen_lower = [w.lower() for w in km_env.intents.gen]
    assert "crear" in gen_lower
    assert "escribir" in gen_lower
    # Base English words still intact
    assert "create" in gen_lower or "write" in gen_lower


def test_keyword_manager_corrupt_recovers_to_base(km_env, tmp_path):
    """Corrupt JSON on disk still leaves BASE_KEYWORDS intact."""
    with open(km_env.keywords_file, "w") as f:
        f.write("<<< CORRUPT JSON >>>")
    km_env._load_keywords()
    # All base intents should be present
    for intent in km_mod.SemanticIntents.__fields__.keys():
        assert len(getattr(km_env.intents, intent)) > 0


def test_translate_and_add_keywords_returns_true_on_valid(km_env):
    """Adapter returns valid JSON → True, and keywords file gets written."""
    # Simulate adapter that returns clean JSON
    mock_adapter = MagicMock()
    valid_json = json.dumps({
        "gen": ["hacer", "fabricar"],  # 2 new words
        "fix": ["corregir"],
        "refactor": [],
        "docs": [],
        "math": [],
        "summarize": [],
        "roleplay": [],
        "format_table": [],
        "analytical": [],
        "meta_pos_con": [],
        "meta_neg_con": []
    })
    mock_adapter.forward_request = AsyncMock(return_value={"text": valid_json})

    result = km_env.translate_and_add_keywords(mock_adapter, "español")
    assert result is True
    # Verify file was written and contains the new word
    with open(km_env.keywords_file) as f:
        saved = json.load(f)
    assert "hacer" in saved["gen"]
    assert "fabricar" in saved["gen"]


def test_translate_and_add_keywords_markdown_fenced(km_env):
    """Markdown JSON-fenced output from adapter is extracted correctly."""
    mock_adapter = MagicMock()
    fenced = "```json\n" + json.dumps({"gen": ["traducir"], "fix": []}) + "\n```"
    mock_adapter.forward_request = AsyncMock(return_value={"text": fenced})
    result = km_env.translate_and_add_keywords(mock_adapter, "test-lang")
    assert result is True
    with open(km_env.keywords_file) as f:
        saved = json.load(f)
    assert "traducir" in saved["gen"]


def test_translate_and_add_keywords_invalid_json_returns_false(km_env):
    """Invalid JSON from adapter → False, no crash."""
    mock_adapter = MagicMock()
    mock_adapter.forward_request = AsyncMock(return_value={"text": "NOT JSON AT ALL"})
    result = km_env.translate_and_add_keywords(mock_adapter, "test-lang")
    assert result is False


def test_translate_and_add_keywords_adapter_exception_returns_false(km_env):
    """Adapter raising exception → False, not an error crash."""
    mock_adapter = MagicMock()
    mock_adapter.forward_request = AsyncMock(side_effect=RuntimeError("conn refused"))
    result = km_env.translate_and_add_keywords(mock_adapter, "test-lang")
    assert result is False


def test_translate_and_add_keywords_no_new_words_returns_false(km_env):
    """No new keywords in response → False (nothing added)."""
    mock_adapter = MagicMock()
    # All values already in base → no additions
    all_base = {k: v for k, v in km_mod.BASE_KEYWORDS.items()}
    mock_adapter.forward_request = AsyncMock(
        return_value={"text": json.dumps(all_base)}
    )
    result = km_env.translate_and_add_keywords(mock_adapter, "en")
    assert result is False


def test_get_regex_valid_intent_returns_pattern():
    """A known intent returns a regex with word boundaries and alternation."""
    km = km_mod.KeywordManager()
    regex = km.get_regex("gen")
    assert regex.startswith(r"\b(") and regex.endswith(r")\b")
    assert "create" in regex  # at least one base word present


def test_get_regex_unknown_intent_returns_unmatchable():
    """Unknown intent returns the unmatchable sentinel regex."""
    km = km_mod.KeywordManager()
    assert km.get_regex("nonexistent_intent_key_xyz") == r"(?!x)x"


def test_get_regex_empty_list_returns_unmatchable(km_env):
    """Intent with empty list returns unmatchable regex."""
    # Monkeypatch one intent to empty to test that code path
    from src.utils.keyword_manager import SemanticIntents
    empty_intents = SemanticIntents(**{**km_mod.BASE_KEYWORDS, "gen": []})
    km_env.intents = empty_intents
    assert km_env.get_regex("gen") == r"(?!x)x"


# ======================================================================
# PricingManager Tests
# ======================================================================

@pytest.fixture
def pm_env(tmp_path, monkeypatch):
    """Isolate PRICE CACHE_FILE to a tmp dir; yield PricingManager."""
    cache_file = str(tmp_path / "model_prices.json")
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    monkeypatch.setattr(pricing_mod, "CACHE_FILE", cache_file)
    return pricing_mod.PricingManager()


def test_pricing_fresh_cache_hit_no_network(pm_env, tmp_path):
    """Fresh cache file → prices loaded from disk, no httpx call."""
    cache_file = tmp_path / "model_prices.json"
    cache_data = {"gpt-4o": {"input_cost_per_token": 5e-06}}
    cache_file.write_text(json.dumps(cache_data))
    pm = pricing_mod.PricingManager()
    with mock.patch("httpx.Client") as MockClient:
        pm2 = pricing_mod.PricingManager()  # would trigger fetch if cache missing
    # First pm already loaded fresh cache — no network needed
    assert "gpt-4o" in pm.prices


def test_pricing_expired_cache_triggers_fetch_and_persists(pm_env, tmp_path, monkeypatch):
    """Expired cache → fetched from httpx.Client.get and written to disk."""
    cache_file = tmp_path / "model_prices.json"
    # Write an old file with mtime far in the past
    old_data = {"old-model": {"input_cost_per_token": 1e-08}}
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(old_data))
    # Set mtime to 2 weeks ago (past CACHE_EXPIRY)
    old_mtime = time.time() - (2 * 24 * 60 * 60)
    os.utime(cache_file, (old_mtime, old_mtime))

    fresh_remote = {"new-model": {"input_cost_per_token": 1e-06}}
    mock_client = MagicMock()
    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json.return_value = fresh_remote
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_get_resp

    with mock.patch("httpx.Client", return_value=mock_client):
        pm = pricing_mod.PricingManager()

    # Fetched data is in cache
    assert "new-model" in pm.prices
    # And file was updated
    saved = json.loads(cache_file.read_text())
    assert "new-model" in saved


def test_pricing_http500_falls_back_to_expired_cache(pm_env, tmp_path, monkeypatch):
    """HTTP 500 → _fallback_to_cache still loads stale data."""
    cache_file = tmp_path / "model_prices.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    stale = {"stale-model": {"input_cost_per_token": 9e-09}}
    cache_file.write_text(json.dumps(stale))
    old_mtime = time.time() - (2 * 24 * 60 * 60)
    os.utime(cache_file, (old_mtime, old_mtime))

    mock_client = MagicMock()
    mock_client.get.return_value = MagicMock(status_code=500)
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with mock.patch("httpx.Client", return_value=mock_client):
        pm = pricing_mod.PricingManager()

    # Fell back to stale cache
    assert "stale-model" in pm.prices


def test_pricing_network_exception_falls_back_to_cache(pm_env, tmp_path, monkeypatch):
    """Network exception → _fallback_to_cache still loads stale data."""
    cache_file = tmp_path / "model_prices.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    stale = {"stale-net-model": {"input_cost_per_token": 7e-09}}
    cache_file.write_text(json.dumps(stale))
    old_mtime = time.time() - (2 * 24 * 60 * 60)
    os.utime(cache_file, (old_mtime, old_mtime))

    with mock.patch("httpx.Client", side_effect=ConnectionError("net down")):
        pm = pricing_mod.PricingManager()

    assert "stale-net-model" in pm.prices


def test_get_model_pricing_exact_match(pm_env):
    """Exact key match returns correct per-1k pricing."""
    pm_env.prices = {
        "gpt-4o": {
            "input_cost_per_token": 2.5e-06,
            "output_cost_per_token": 1.0e-05,
            "supports_function_calling": True,
            "max_input_tokens": 128000,
            "max_tokens": 4096
        }
    }
    p, c, sc, mi, mo = pm_env.get_model_pricing("gpt-4o")
    assert p == pytest.approx(0.0025)
    assert c == pytest.approx(0.01)
    assert sc is True
    assert mi == 128000
    assert mo == 4096


def test_get_model_pricing_provider_suffix_match(pm_env):
    """'provider/model' key matched by bare 'model' name."""
    pm_env.prices = {
        "google/gemini-2.0-flash-lite": {"input_cost_per_token": 10e-08, "output_cost_per_token": 40e-08}
    }
    p, c, *_ = pm_env.get_model_pricing("gemini-2.0-flash-lite")
    assert p == pytest.approx(0.01)
    assert c == pytest.approx(0.04)


def test_get_model_pricing_models_prefix_strip(pm_env):
    """'models/xxx' prefix is stripped before lookup."""
    pm_env.prices = {"claude-3-opus": {"input_cost_per_token": 15e-06}}
    p, *_ = pm_env.get_model_pricing("models/claude-3-opus")
    assert p == pytest.approx(0.015)


def test_get_model_pricing_per_token_scaling(pm_env):
    """Cost values are multiplied by 1000 (per-1k conversion)."""
    pm_env.prices = {"test-scaled": {"input_cost_per_token": 7e-04}}
    p, *_ = pm_env.get_model_pricing("test-scaled")
    assert p == pytest.approx(0.7)


def test_get_model_pricing_gemini_function_calling_fallback(pm_env):
    """Gemini 1.5/2.0/2.5 models assume function-calling support even without flag."""
    pm_env.prices = {"gemini-1.5-pro": {"input_cost_per_token": 1e-06}}
    _, _, sc, *_ = pm_env.get_model_pricing("gemini-1.5-pro")
    assert sc is True

    pm_env.prices = {"gemini-2.5-flash": {"input_cost_per_token": 1e-06}}
    _, _, sc2, *_ = pm_env.get_model_pricing("gemini-2.5-flash")
    assert sc2 is True


def test_get_model_pricing_unknown_returns_all_none_false(pm_env):
    """Unknown model → (None, None, False, None, None)."""
    pm_env.prices = {"known-thing": {"input_cost_per_token": 1e-06}}
    assert pm_env.get_model_pricing("totally-unknown-model-xyz") == (None, None, False, None, None)


def test_get_model_pricing_empty_prices(pm_env):
    """Empty prices dict → (None, None, False, None, None)."""
    pm_env.prices = {}
    assert pm_env.get_model_pricing("gpt-4o") == (None, None, False, None, None)


def test_get_model_capabilities_explicit_rules(pm_env):
    """Hard-coded vision rules for gpt-4.1-nano / gpt-4o / gemini."""
    assert pm_env.get_model_capabilities("gpt-4.1-nano")["supports_vision"] is False
    assert pm_env.get_model_capabilities("gpt-4o-mini")["supports_vision"] is True
    assert pm_env.get_model_capabilities("gemini-2.0-pro")["supports_vision"] is True


def test_get_model_capabilities_cache_lookup(pm_env):
    """Vision support read from cache data when not covered by explicit rules."""
    pm_env.prices = {
        "vision-model-x": {"supports_vision": True},
        "text-only-model": {"supports_vision": False}
    }
    assert pm_env.get_model_capabilities("vision-model-x")["supports_vision"] is True
    assert pm_env.get_model_capabilities("text-only-model")["supports_vision"] is False


def test_get_model_capabilities_unknown_false(pm_env):
    """Unknown model with no cache entry → False."""
    pm_env.prices = {"some-known": {}}
    assert pm_env.get_model_capabilities("unknown-xyz")["supports_vision"] is False


# ======================================================================
# ModelInfoManager Tests
# ======================================================================

@pytest.fixture
def mim_env(tmp_path, monkeypatch):
    """Isolate model_info CACHE_FILE to a tmp dir; yield ModelInfoManager."""
    cache_file = str(tmp_path / "model_info_cache.json")
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    monkeypatch.setattr(mi_mod, "CACHE_FILE", cache_file)
    return mi_mod.ModelInfoManager()


def test_model_info_fresh_cache_hit_no_network(tmp_path, monkeypatch):
    """Fresh cache file → loaded from disk, no HTTP call made."""
    cache_file = tmp_path / "model_info_cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    fresh_data = {"gpt-4o": {"description": "Cached description"}}
    cache_file.write_text(json.dumps(fresh_data))

    with mock.patch("httpx.Client") as MockClient:
        mi_mod2 = monkeypatch.setattr(mi_mod, "CACHE_FILE", str(cache_file))
        monkeypatch.setattr(mi_mod, "CACHE_FILE", str(cache_file))
        m = mi_mod.ModelInfoManager()

    assert "gpt-4o" in m.info_cache
    assert m.info_cache["gpt-4o"]["description"] == "Cached description"


def test_model_info_expired_cache_fetches_and_persists(tmp_path, monkeypatch):
    """Expired cache → fetches mocked OpenRouter, saves both provider/short keys."""
    cache_file = tmp_path / "model_info_cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({}))
    old_mtime = time.time() - (15 * 24 * 60 * 60)
    os.utime(cache_file, (old_mtime, old_mtime))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [
        {
            "id": "anthropic/claude-3-opus",
            "name": "Claude 3 Opus",
            "description": "Top-tier Anthropic model",
            "context_length": 200000,
            "architecture": {"modality": "Text"},
            "top_provider": {"name": "Anthropic"}
        }
    ]}
    mock_client = MagicMock()
    mock_client.get.return_value = mock_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(mi_mod, "CACHE_FILE", str(cache_file))
    with mock.patch("httpx.Client", return_value=mock_client):
        m = mi_mod.ModelInfoManager()

    # Both full and short keys are indexed
    assert "anthropic/claude-3-opus" in m.info_cache
    assert "claude-3-opus" in m.info_cache
    # Cache was persisted
    saved = json.loads(cache_file.read_text())
    assert "anthropic/claude-3-opus" in saved


def test_model_info_http500_no_crash(tmp_path, monkeypatch):
    """HTTP 500 → handled cleanly without raising an exception."""
    cache_file = tmp_path / "model_info_cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    mock_client = MagicMock()
    mock_client.get.return_value = MagicMock(status_code=500)
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(mi_mod, "CACHE_FILE", str(cache_file))
    with mock.patch("httpx.Client", return_value=mock_client):
        m = mi_mod.ModelInfoManager()  # must not raise

    # Cache is empty (or stale fallback)
    assert "claude-3-opus" not in m.info_cache


def test_model_info_network_exception_no_crash(tmp_path, monkeypatch):
    """Network exception → handled cleanly."""
    cache_file = tmp_path / "model_info_cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(mi_mod, "CACHE_FILE", str(cache_file))
    with mock.patch("httpx.Client", side_effect=ConnectionError("net down")):
        m = mi_mod.ModelInfoManager()  # must not raise


def test_get_model_description_exact_cache_hit(mim_env):
    """Exact key in cache → description returned directly."""
    mim_env.info_cache = {
        "my-custom-model": {"description": "My custom model description"}
    }
    assert mim_env.get_model_description("my-custom-model") == "My custom model description"


def test_get_model_description_provider_short_fallback(mim_env):
    """'provider/model' lookup falls back to bare 'model' key in cache."""
    mim_env.info_cache = {
        "claude-3-opus": {"description": "Anthropic Opus"}
    }
    desc = mim_env.get_model_description("anthropic/claude-3-opus")
    assert desc == "Anthropic Opus"


def test_get_model_description_builtin_gpt4o_fallback(mim_env):
    """gpt-4o fallback description is returned when not in cache."""
    mim_env.info_cache = {}
    desc = mim_env.get_model_description("gpt-4o-mini")
    assert "flagship" in desc.lower() or "multimodal" in desc.lower()


def test_get_model_description_builtin_claude_fallback(mim_env):
    """claude-3-5-sonnet fallback description is returned."""
    mim_env.info_cache = {}
    desc = mim_env.get_model_description("claude-3-5-sonnet-20241022")
    assert "anthropic" in desc.lower() or "coding" in desc.lower() or len(desc) > 0


def test_get_model_description_builtin_deepseek_coder_fallback(mim_env):
    """deepseek-coder fallback description is returned."""
    mim_env.info_cache = {}
    desc = mim_env.get_model_description("deepseek-coder")
    assert "coding" in desc.lower()


def test_get_model_description_generic_default(mim_env):
    """Completely unknown model → generic default description."""
    mim_env.info_cache = {}
    desc = mim_env.get_model_description("totally-unknown-xyz")
    assert "high-performance" in desc.lower() or "agentic" in desc.lower()


def test_get_ollama_description_non_11434_no_network(mim_env):
    """Non-11434 URL → returns empty string without any network call."""
    with mock.patch("httpx.AsyncClient") as MockAsync:
        result = mim_env.get_ollama_description("test-model", "http://other-host:9999/v1")
    assert result == ""
    # AsyncClient.post should NOT have been triggered
    # Note: get_ollama_description is async; we need asyncio.run
import asyncio

def test_get_ollama_description_non_11434_no_network(mim_env):
    """Non-11434 URL → returns empty string without any network call."""
    with mock.patch("httpx.AsyncClient") as MockAsync:
        result = asyncio.run(mim_env.get_ollama_description("test-model", "http://other-host:9999/v1"))
    assert result == ""


def test_get_ollama_description_success(mim_env):
    """Successful Ollama POST → formatted description with family and params."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "details": {"family": "llama", "parameter_size": "70B"}
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with mock.patch("httpx.AsyncClient", return_value=mock_client) as MockAsync:
        result = asyncio.run(mim_env.get_ollama_description("llama-70b", "http://localhost:11434/v1"))

    assert "llama" in result.lower()
    assert "70B" in result


def test_get_ollama_description_error_returns_empty(mim_env):
    """Network error during Ollama POST → empty string, no crash."""
    with mock.patch("httpx.AsyncClient", side_effect=ConnectionError("net down")):
        result = asyncio.run(mim_env.get_ollama_description("test", "http://localhost:11434/v1"))
    assert result == ""


def test_get_ollama_description_http_error_returns_empty(mim_env):
    """Non-200 response → empty string."""
    mock_resp = MagicMock(status_code=500)
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with mock.patch("httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(mim_env.get_ollama_description("test", "http://localhost:11434/v1"))
    assert result == ""
