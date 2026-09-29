"""Selection rules for the OpenRouter and LM Studio providers.

OpenRouter resells ~450 models, so what matters is not that discovery works
but that it registers a *defensible subset*. These tests pin the rules that
keep the pool useful: no duplicates of providers you already reach directly,
nothing priced at $0, and a cap so one prolific vendor cannot flood it.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.router import core as core_mod


def _model(mid, prompt="0.000001", completion="0.000002", tools=True, created=1000,
           out=("text",)):
    return {
        "id": mid,
        "created": created,
        "pricing": {"prompt": prompt, "completion": completion},
        "supported_parameters": ["tools"] if tools else ["temperature"],
        "architecture": {"output_modalities": list(out)},
        "context_length": 128000,
    }


CATALOGUE = [
    _model("openai/gpt-5.2", created=90),
    _model("anthropic/claude-sonnet-5.5", created=89),
    _model("meta-llama/llama-4-maverick", created=88),
    _model("meta-llama/llama-4-scout", created=87),
    _model("meta-llama/llama-3.9-8b", created=86),
    _model("nvidia/nemotron-nano-12b-v2", created=85),
    _model("minimax/minimax-m2:free", prompt="0", completion="0", created=84),
    _model("stealth/space-bunny-alpha", prompt="0", completion="0", created=83),
    _model("someone/chat-only-model", tools=False, created=82),
    _model("someone/image-maker", out=("image",), created=81),
]


class _Recorder:
    """Stands in for RouterCore, capturing what discovery would register."""

    def __init__(self):
        self.models = {}
        self.adapters = {}
        self.all_discovered_models = []
        self.added = []
        self.load_balancer = SimpleNamespace(add_model=lambda *a, **k: None)

    # Signature copied verbatim from RouterCore.add_model. Keep it that way:
    # a mock that accepts anything will happily swallow arguments passed in the
    # wrong order, which is exactly the bug this file is meant to catch.
    def add_model(
        self,
        model_id,
        model_name,
        cost,
        time,
        probability,
        concurrency_limit=None,
        prompt_cost=None,
        completion_cost=None,
        supports_function_calling=False,
        max_input_tokens=None,
        max_tokens=None,
    ):
        self.added.append(
            {
                "name": model_id,
                "cost": cost,
                "p_cost": prompt_cost,
                "c_cost": completion_cost,
                "tools": supports_function_calling,
                "max_input_tokens": max_input_tokens,
            }
        )
        self.models[model_id] = True

    _discover_openrouter = core_mod.RouterCore._discover_openrouter
    _discover_lm_studio = core_mod.RouterCore._discover_lm_studio


def _settings(**over):
    base = dict(
        disabled_models=[],
        openrouter_models=None,
        openrouter_max_per_vendor=2,
        openrouter_allow_free=None,
        openai_api_key=None,
        anthropic_api_key=None,
        gemini_api_key=None,
        mistral_api_key=None,
        deepseek_api_key=None,
        moonshot_api_key=None,
        zai_api_key=None,
        xai_api_key=None,
        dashscope_api_key=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


@contextmanager
def _stubbed(resp):
    """Stub the provider call, the pricing table and the adapter.

    The adapter matters: constructing a real LiteLLMAdapter fetches LiteLLM's
    model cost map over the same httpx we are patching, which fails in a way
    that has nothing to do with the code under test.
    """
    import src.adapters.litellm_adapter as adapter_mod

    with patch.object(core_mod.httpx, "get", return_value=resp), patch.object(
        core_mod.pricing_manager,
        "get_model_pricing",
        return_value=(None, None, True, None, None),
    ), patch.object(adapter_mod, "LiteLLMAdapter", lambda **kw: SimpleNamespace(**kw)):
        yield


def _run_openrouter(settings, catalogue=CATALOGUE):
    rec = _Recorder()
    resp = SimpleNamespace(status_code=200, json=lambda: {"data": catalogue})
    with _stubbed(resp):
        rec._discover_openrouter(settings=settings, api_key="sk-or-test")
    return [a["name"] for a in rec.added], rec


def test_zero_priced_models_are_never_registered():
    """$0 wins every routing decision on cost, then rate limits you."""
    names, _ = _run_openrouter(_settings())
    assert not any("space-bunny" in n for n in names)
    assert not any(n.endswith(":free") for n in names)


def test_vendors_with_a_direct_key_are_skipped():
    names, _ = _run_openrouter(_settings(openai_api_key="sk-live"))
    assert not any("openai/gpt" in n for n in names)
    # Anthropic has no direct key here, so it stays.
    assert any("anthropic/claude" in n for n in names)


def test_tilde_alias_vendors_are_deduped_too():
    """OpenRouter's "~vendor" aliases are the same vendor.

    ~openai/gpt-sol-latest is a floating pointer to OpenAI's current model. It
    must be skipped when OPENAI_API_KEY is set, exactly as openai/* is --
    otherwise the one thing the dedupe exists to prevent, the same model at two
    prices, walks straight through the alias namespace.
    """
    names, _ = _run_openrouter(
        _settings(openai_api_key="sk-live"),
        catalogue=[
            _model("~openai/gpt-sol-latest"),
            _model("~anthropic/claude-haiku-latest"),
        ],
    )
    assert not any("openai" in n for n in names)
    # Anthropic has no direct key here, so its alias stays.
    assert any("anthropic" in n for n in names)


def test_per_vendor_cap_bounds_a_prolific_vendor():
    names, _ = _run_openrouter(_settings(openrouter_max_per_vendor=2))
    assert len([n for n in names if "meta-llama" in n]) == 2


def test_chat_only_and_non_text_models_are_skipped():
    names, _ = _run_openrouter(_settings())
    assert not any("chat-only" in n for n in names)
    assert not any("image-maker" in n for n in names)


def test_allowlist_overrides_the_gap_filler():
    names, _ = _run_openrouter(
        _settings(openai_api_key="sk-live", openrouter_models="gpt-5.2")
    )
    assert names == ["openrouter/openai/gpt-5.2"]


def test_naming_a_free_model_explicitly_overrides_the_ban():
    """A safe default, not a prohibition: if you ask for it by name, you get it."""
    names, _ = _run_openrouter(_settings(openrouter_models="minimax-m2"))
    assert names == ["openrouter/minimax/minimax-m2:free"]


def test_allow_free_flag_lifts_the_ban_for_the_default_mode():
    names, _ = _run_openrouter(_settings(openrouter_allow_free="true"))
    assert any(n.endswith(":free") for n in names)
    assert any("space-bunny" in n for n in names)


def test_all_still_drops_zero_priced_models():
    names, _ = _run_openrouter(_settings(openrouter_models="all"))
    # 10 in the catalogue, minus the two priced at $0 and the image-only one.
    # "all" deliberately keeps the chat-only model: it only waives the
    # gap-filler rules, not the two hard exclusions.
    assert len(names) == 7
    assert not any("space-bunny" in n for n in names)
    assert any("chat-only" in n for n in names)
    assert not any("image-maker" in n for n in names)


def test_pricing_comes_from_openrouter_converted_to_per_1k():
    _, rec = _run_openrouter(_settings(openrouter_models="nemotron"))
    entry = rec.added[0]
    # 0.000001 USD/token -> 0.001 USD per 1K tokens
    assert entry["p_cost"] == pytest.approx(0.001)
    assert entry["c_cost"] == pytest.approx(0.002)


def test_add_model_receives_costs_in_the_right_positions():
    """Guards the argument order of RouterCore.add_model.

    add_model takes (id, name, cost, time, probability, concurrency_limit,
    prompt_cost, completion_cost, ...). Passing costs where time and
    probability belong silently stores token limits as prices, which only
    shows up as nonsense on a live router.
    """
    _, rec = _run_openrouter(_settings(openrouter_models="nemotron"))
    e = rec.added[0]
    assert e["p_cost"] == pytest.approx(0.001)
    assert e["c_cost"] == pytest.approx(0.002)
    assert e["tools"] is True
    assert e["max_input_tokens"] == 128000


def test_lm_studio_add_model_argument_positions():
    rec = _Recorder()
    resp = SimpleNamespace(status_code=200, json=lambda: {"data": [{"id": "m1"}]})
    with _stubbed(resp):
        rec._discover_lm_studio(
            settings=_settings(),
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
        )
    e = rec.added[0]
    assert (e["p_cost"], e["c_cost"], e["cost"]) == (0.0, 0.0, 0.0)
    assert e["tools"] is True


def test_models_are_namespaced_under_openrouter():
    names, _ = _run_openrouter(_settings(openrouter_models="nemotron"))
    assert names == ["openrouter/nvidia/nemotron-nano-12b-v2"]


def test_lm_studio_registers_local_models_as_free():
    rec = _Recorder()
    resp = SimpleNamespace(
        status_code=200,
        json=lambda: {"data": [{"id": "qwen3-8b"}, {"id": "gemma-3-12b"}]},
    )
    with _stubbed(resp):
        rec._discover_lm_studio(
            settings=_settings(),
            base_url="http://localhost:1234",
            api_key="lm-studio",
        )
    assert [a["name"] for a in rec.added] == ["lmstudio/qwen3-8b", "lmstudio/gemma-3-12b"]
    assert all(a["cost"] == 0.0 for a in rec.added)


def test_lm_studio_absent_is_not_an_error():
    """Nothing is listening on the default port for most installs."""
    rec = _Recorder()
    with patch.object(core_mod.httpx, "get", side_effect=OSError("connection refused")):
        rec._discover_lm_studio(
            settings=_settings(),
            base_url="http://localhost:1234/v1",
            api_key="lm-studio",
        )
    assert rec.added == []
    assert rec.all_discovered_models == []
