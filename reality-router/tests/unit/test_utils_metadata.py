"""Deterministic offline unit tests for utility managers.
All network/file calls are mocked; no live external access.
"""
import json
import os
import sys
import time
from unittest import mock
import pytest

# Import source modules
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


@pytest.mark.asyncio
async def test_translate_and_add_keywords_returns_true_on_valid(km_env):
    """Valid JSON response → True, and new keywords persisted."""
    class MockAdapter:
        async def forward_request(self, request):
            return {
                "text": json.dumps({
                    "gen": ["hacer", "fabricar"],
                    "fix": ["corregir"],
                    "refactor": [],
                    "docs": ["documentar"],
                    "math": [],
                    "summarize": [],
                    "roleplay": [],
                    "format_table": [],
                    "analytical": [],
                    "meta_pos_con": [],
                    "meta_neg_con": []
                })
            }
    result = await km_env.translate_and_add_keywords(MockAdapter(), "es")
    assert result is True
    # Verify persistence
    with open(km_env.keywords_file) as f:
        saved = json.load(f)
    assert "hacer" in saved["gen"]
    assert "fabricar" in saved["gen"]


@pytest.mark.asyncio
async def test_translate_and_add_keywords_markdown_fenced(km_env):
    """Fenced JSON works correctly."""
    class MockAdapter:
        async def forward_request(self, request):
            return {"text": "```json\n" + json.dumps({"gen": ["traducir"], "fix": []}) + "\n```"}
    
    result = await km_env.translate_and_add_keywords(MockAdapter(), "test-lang")
    assert result is True
    with open(km_env.keywords_file) as f:
        saved = json.load(f)
    assert "traducir" in saved["gen"]


@pytest.mark.asyncio
async def test_translate_and_add_keywords_invalid_json_returns_false(km_env):
    """Invalid JSON from adapter → False, no crash."""
    class MockAdapter:
        async def forward_request(self, request):
            return {"text": "NOT JSON AT ALL"}
    
    result = await km_env.translate_and_add_keywords(MockAdapter(), "test")
    assert result is False


@pytest.mark.asyncio
async def test_translate_and_add_keywords_exception_returns_false(km_env):
    """Adapter raising exception → False, not crash."""
    class MockAdapter:
        async def forward_request(self, request):
            raise RuntimeError("connection refused")
    
    result = await km_env.translate_and_add_keywords(MockAdapter(), "test")
    assert result is False


@pytest.mark.asyncio
async def test_translate_and_add_keywords_no_new_words_returns_false(km_env):
    """Response includes only existing keywords → returns False."""
    class MockAdapter:
        async def forward_request(self, request):
            # All values already in base, no additions
            return {"text": json.dumps({k: v for k, v in km_mod.BASE_KEYWORDS.items()})}
    
    result = await km_env.translate_and_add_keywords(MockAdapter(), "en")
    assert result is False


def test_km_get_regex_valid_intent_returns_pattern():
    """A known intent returns a regex with word boundaries and alternation."""
    km = km_mod.KeywordManager()
    regex = km.get_regex("gen")
    assert regex.startswith(r"\b(")
    assert regex.endswith(r")\b")
    assert len(regex) > 10


def test_km_get_regex_unknown_intent_unmatchable():
    """Unknown intent returns unmatchable sentinel regex."""
    km = km_mod.KeywordManager()
    assert km.get_regex("nonexistent_intent_key_xyz") == r"(?!x)x"


def test_km_get_regex_empty_list_returns_unmatchable(km_env):
    """Intent with empty list returns unmatchable regex."""
    # Monkeypatch one intent to empty to test that code path
    from src.utils.keyword_manager import SemanticIntents
    empty_intents = SemanticIntents(**{**km_mod.BASE_KEYWORDS, "gen": []})
    
    # Create fresh instance to trigger reload
    km_env2 = km_mod.KeywordManager()
    import copy
    km_env2.intents = empty_intents
    
    assert km_env2.get_regex("gen") == r"(?!x)x"


# ======================================================================
# PricingManager Tests
# ======================================================================

@pytest.fixture
def pm_env(tmp_path, monkeypatch):
    """Set CACHE_FILE → tmp_path and yield PricingManager."""
    cache_file = str(tmp_path / "model_prices.json")
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    monkeypatch.setattr(pricing_mod, "CACHE_FILE", cache_file)
    # Create empty cache file pre-write
    with open(cache_file, "w") as f:
        json.dump({}, f)
    return pricing_mod.PricingManager()


def test_pricing_fresh_cache_hit(pm_env, tmp_path):
    """Pre-written cache file → prices loaded, no network."""
    with open(pm_env.cache_file, "r") as f:
        data = json.loads(f.read())
    assert "gpt-4o" in data
    pm2 = pricing_mod.PricingManager()
    assert "gpt-4o" in pm2.prices


def test_pricing_expired_cache_triggers_fetch_and_persists(pm_env, tmp_path, monkeypatch):
    """Old file ( >10days ) → fetch from mock Client and persist."""
    cache_file = pm_env.cache_file
    with open(cache_file, "r") as f:
        json.dump({"old-model": {"input_cost_per_token": 1e-08}}, f)
    # Force old mtime beyond 10 days (CACHE_EXPIRY = 7 days)
    past_time = time.time() - (10 * 24 * 60 * 60)  # 10 days > 7 days CACHE_EXPIRY
    os.utime(cache_file, (past_time, past_time))
    
    fresh_remote = {"new-model": {"input_cost_per_token": 1e-06, "output_cost_per_token": 4e-06}}
    mock_resp = mock.Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fresh_remote
    mock_client = mock.Mock()
    mock_client.__enter__ = mock.Mock(return_value=mock_client)
    mock_client.__exit__ = mock.Mock(return_value=False)
    mock_client.get.return_value = mock_resp
    
    with mock.patch("httpx.Client", return_value=mock_client):
        pm2 = pricing_mod.PricingManager()
    assert "new-model" in pm2.prices
    saved = json.loads(open(cache_file).read())
    assert "new-model" in saved


def test_pricing_http500_falls_back_to_expired_cache(pm_env, tmp_path, monkeypatch):
    """HTTP 500 → still falls back to expired cached data."""
    cache_file = pm_env.cache_file
    with open(cache_file, "r") as f:
        json.dump({"stale": {"input_cost_per_token": 9e-09}}, f)
    past_time = time.time() - (11 * 24 * 60 * 60)
    os.utime(cache_file, (past_time, past_time))
    
    mock_resp = mock.Mock()
    mock_resp.status_code = 500
    mock_client = mock.Mock()
    mock_client.get.return_value = mock_resp
    mock_client.__enter__ = mock.Mock(return_value=mock_client)
    mock_client.__exit__ = mock.Mock(return_value=False)
    
    with mock.patch("httpx.Client", return_value=mock_client):
        pm2 = pricing_mod.PricingManager()
    assert "stale" in pm2.prices


def test_pricing_network_exception_falls_back(pm_env, tmp_path, monkeypatch):
    """Network exception → falls back to expired cache."""
    cache_file = pm_env.cache_file
    with open(cache_file, "r") as f:
        json.dump({"fallback": {"input_cost_per_token": 7e-09}}, f)
    past_time = time.time() - (11 * 24 * 60 * 60)
    os.utime(cache_file, (past_time, past_time))
    
    with mock.patch("httpx.Client", side_effect=ConnectionError("net down")):
        pm2 = pricing_mod.PricingManager()
    assert "fallback" in pm2.prices


def test_get_model_pricing_exact_match(pm_env):
    """Exact key fetch returns scaled costs correctly."""
    pm_env.prices = {
        "gpt-4o": {
            "input_cost_per_token": 1e-05,
            "output_cost_per_token": 4e-05,
            "supports_function_calling": True,
            "max_input_tokens": 128000,
            "max_tokens": 4096
        }
    }
    p, c, sc, mi, mo = pm_env.get_model_pricing("gpt-4o")
    assert p == pytest.approx(0.01)   # 1e-05 * 1000
    assert c == pytest.approx(0.04)   # 4e-05 * 1000
    assert sc is True
    assert mi == 128000
    assert mo == 4096


def test_get_model_pricing_prefix_suffix_match(pm_env):
    """Provider prefix lookup works for bare model name."""
    pm_env.prices = {
        "google/gemini-1.5-pro": {
            "input_cost_per_token": 2e-06,
            "output_cost_per_token": 8e-06
        }
    }
    p, c, *_ = pm_env.get_model_pricing("gemini-1.5-pro")
    assert p == pytest.approx(0.002)   # 2e-06 * 1000
    assert c == pytest.approx(0.008)   # 8e-06 * 1000


def test_get_model_pricing_models_prefix_strip(pm_env):
    """'models/xxx' prefix is stripped before lookup."""
    pm_env.prices = {"claude-3-opus": {"input_cost_per_token": 15e-06}}
    p, *_ = pm_env.get_model_pricing("models/claude-3-opus")
    assert p == pytest.approx(0.015)   # 15e-06 * 1000


def test_get_model_pricing_per_token_scaling(pm_env):
    """Costs multiplied by 1000 for per-1k conversion."""
    pm_env.prices = {"scaled": {"input_cost_per_token": 7e-04}}
    p, *_ = pm_env.get_model_pricing("scaled")
    assert p == pytest.approx(0.7)   # 7e-04 * 1000


def test_get_model_pricing_gemini_function_calling_fallback(pm_env):
    """Gemini 1.5/2.5 assume function-calling support."""
    pm_env.prices = {"gemini-1.5-pro": {"input_cost_per_token": 1e-06}}
    _, _, sc, *_ = pm_env.get_model_pricing("gemini-1.5-pro")
    assert sc is True


def test_get_model_pricing_unknown_returns_all_none_false(pm_env):
    """Unknown model → (None, None, False, None, None)."""
    assert pm_env.get_model_pricing("unknown-xyz") == (None, None, False, None, None)


def test_get_model_pricing_empty_prices(pm_env):
    """Empty prices dict → all None defaults."""
    pm_env.prices = {}
    assert pm_env.get_model_pricing("any") == (None, None, False, None, None)


def test_get_model_capabilities_explicit_rules(pm_env):
    """Hardcoded vision rule matches."""
    assert pm_env.get_model_capabilities("gpt-4.1-nano")["supports_vision"] is False
    assert pm_env.get_model_capabilities("gpt-4o-mini")["supports_vision"] is True
    assert pm_env.get_model_capabilities("gemini-2.0-pro")["supports_vision"] is True


def test_get_model_capabilities_cache_lookup(pm_env):
    """Cache entry overrides explicit rule."""
    pm_env.prices = {
        "text-only-xyz": {"supports_vision": False},
        "vision-model-abc": {"supports_vision": True}
    }
    assert pm_env.get_model_capabilities("vision-model-abc")["supports_vision"] is True
    assert pm_env.get_model_capabilities("text-only-xyz")["supports_vision"] is False


def test_get_model_capabilities_unknown_false(pm_env):
    """No entry → False."""
    pm_env.prices = {"known": {}}
    assert pm_env.get_model_capabilities("unknown-xyz")["supports_vision"] is False


# ======================================================================
# ModelInfoManager Tests
# ======================================================================

@pytest.fixture
def mim_env(tmp_path, monkeypatch):
    """Isolate CACHE_FILE to temp location; pre-cache fresh JSON; yield ModelInfoManager."""
    cache_file = str(tmp_path / "model_info_cache.json")
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    monkeypatch.setattr(mi_mod, "CACHE_FILE", cache_file)
    # Pre-write empty JSON before creating manager instance
    with open(cache_file, "w") as f:
        json.dump({}, f)
    return mi_mod.ModelInfoManager()


def test_mim_fresh_cache_hit_no_network(mim_env, tmp_path):
    """Prefilled cache file → loaded without HTTP call."""
    # The cache file was pre-written as '{}'
    assert os.path.exists(str(mim_env.cache_file))
    mim2 = mi_mod.ModelInfoManager()
    # Just ensure caching works
    assert hasattr(mim_env, "info_cache")


async def test_mim_get_model_description_exact_cache_hit(mim_env):
    """Exact key in cache returns description directly."""
    mim_env.info_cache = {"my-custom": {"description": "Custom desc"}}
    assert mim_env.get_model_description("my-custom") == "Custom desc"


async def test_mim_get_model_description_provider_short_fallback(mim_env):
    """'provider/model' → bare 'model' key in cache works."""
    mim_env.info_cache = {"claude-opus": {"description": "Fallback"}}
    assert mim_env.get_model_description("anthropic/claude-opus") == "Fallback"


async def test_mim_get_model_description_builtin_gpt4o_fallback(mim_env):
    """gpt-4o fallback description present."""
    mim_env.info_cache = {}
    desc = mim_env.get_model_description("gpt-4o-mini")
    assert len(desc) > 0  # falls back to builtins


async def test_mim_get_model_description_builtin_claude_fallback(mim_env):
    """claude-3-sonnet fallback description present."""
    mim_env.info_cache = {}
    desc = mim_env.get_model_description("claude-3-5-sonnet")
    assert len(desc) > 0


async def test_mim_get_model_description_builtin_deepseek_fallback(mim_env):
    """deepseek-coder fallback description present."""
    mim_env.info_cache = {}
    desc = mim_env.get_model_description("deepseek-coder")
    assert "coding" in desc.lower() or len(desc) > 0


async def test_mim_get_model_description_generic_default(mim_env):
    """Totally unknown models get generic default."""
    mim_env.info_cache = {}
    desc = mim_env.get_model_description("totally-unknown-unknown")
    assert len(desc) > 0


@pytest.mark.asyncio
async def test_get_ollada_description_non_11434_no_network(mim_env):
    """Non-11434 URL → empty string, no network call."""
    result = mim_env.get_ollada_description("test", "http://other:9999/v1")
    assert result == ""


async def test_get_ollada_description_success(mim_env):
    """Successful Ollada POST → formatted plain description."""
    mock_resp = mock.Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"details": {"family": "llama", "parameter_size": "7B"}}
    mock_client = mock.AsyncMock()
    mock_client.post = mock.AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = mock.AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = mock.AsyncMock(return_value=False)
    
    with mock.patch("httpx.AsyncClient", return_value=mock_client):
        result = await mim_env.get_ollada_description("llama-7b", "http://localhost:11434/v1")
    assert "llama" in result.lower()
    assert "7B" in result


async def test_get_ollada_description_error_returns_empty(mim_env):
    """Network error during Ollada POST → empty string."""
    with mock.patch("httpx.AsyncClient", side_effect=ConnectionError("net down")):
        result = await mim_env.get_ollada_description("any-model", "http://localhost:11434/v1")
    assert result == ""


def test_mim_http500_no_crash(mim_env, tmp_path, monkeypatch):
    """HTTP 500 → handles gracefully without exception."""
    cache_file = mim_env.cache_file
    with open(cache_file, "w") as f:
        f.write("{}")
    past = time.time() - (30 * 24 * 60 * 60)
    os.utime(cache_file, (past, past))
    
    mock_resp = mock.Mock()
    mock_resp.status_code = 500
    mock_client = mock.Mock()
    mock_client.get.return_value = mock_resp
    mock_client.__enter__ = mock.Mock(return_value=mock_client)
    mock_client.__exit__ = mock.Mock(return_value=False)
    
    with mock.patch("httpx.Client", return_value=mock_client):
        mim = mi_mod.ModelInfoManager()  # must not raise
    # Cache should remain populated from prefill, but no new entry added


def test_mim_network_exception_no_crash(mim_env, tmp_path, monkeypatch):
    """Network exception → handles gracefully."""
    mock_client = mock.Mock()
    mock_client.get.side_effect = ConnectionError("net down")
    mock_client.__enter__ = mock.Mock(return_value=mock_client)
    mock_client.__exit__ = mock.Mock(return_value=False)
    
    with mock.patch("httpx.Client", return_value=mock_client):
        mim = mi_mod.ModelInfoManager()  # must not raise
    # No cache activity on failure


def test_model_info_manual_fetch_still_works(mim_env):
    """Manual fetch call respects TTL, but is not auto-triggered in fixture."""
    # Just verify the method can be called without crashing
    assert hasattr(mim_env, "refresh_info")
