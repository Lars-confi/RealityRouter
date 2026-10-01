"""Where a recorded cost comes from.

The router's cost column feeds the dashboard, the billing alert and the
cost-vs-time trade-off a user tunes with the alpha/beta slider. A price-table
estimate is not good enough for any of those once prompt caching is in play:
on an OpenRouter pool with warm caches, litellm's estimate overstated real
spend by 1.67x overall and by 12x on a single model, while understating
another by half. These tests pin the preference order so a provider's own
figure is never silently replaced by a recomputation.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.adapters.litellm_adapter import resolve_cost


def _response(usage_cost=None, hidden_cost=None):
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10)
    if usage_cost is not None:
        usage.cost = usage_cost
    r = SimpleNamespace(usage=usage)
    if hidden_cost is not None:
        r._hidden_params = {"response_cost": hidden_cost}
    return r


def test_provider_cost_wins_over_estimate():
    """usage.cost is the invoice; the estimate must not override it."""
    r = _response(usage_cost=0.000218)
    with patch("src.adapters.litellm_adapter.litellm.completion_cost",
               return_value=0.00148):  # what the price table would have said
        cost, source = resolve_cost(r)
    assert cost == pytest.approx(0.000218)
    assert source == "provider"


def test_provider_cost_wins_even_when_hidden_params_present():
    r = _response(usage_cost=0.000218, hidden_cost=0.000218)
    cost, source = resolve_cost(r)
    assert cost == pytest.approx(0.000218)
    assert source == "provider"


def test_falls_back_to_litellm_resolved_cost():
    """No usage.cost, but litellm resolved one -- use it, and say so."""
    r = _response(hidden_cost=0.0042)
    with patch("src.adapters.litellm_adapter.litellm.completion_cost",
               return_value=0.0099):
        cost, source = resolve_cost(r)
    assert cost == pytest.approx(0.0042)
    assert source == "litellm_resolved"


def test_falls_back_to_estimate_when_provider_reports_nothing():
    r = _response()
    with patch("src.adapters.litellm_adapter.litellm.completion_cost",
               return_value=0.0031):
        cost, source = resolve_cost(r)
    assert cost == pytest.approx(0.0031)
    assert source == "litellm_estimate"


def test_zero_is_a_real_cost_not_a_missing_one():
    """A free model, or a filtered call that consumed nothing, bills zero.

    Treating 0 as absent would send these to the estimate, which invents a
    charge for tokens that were never served.
    """
    r = _response(usage_cost=0.0)
    with patch("src.adapters.litellm_adapter.litellm.completion_cost",
               return_value=0.0077):
        cost, source = resolve_cost(r)
    assert cost == 0.0
    assert source == "provider"


@pytest.mark.parametrize("bad", [None, "0.004", -1.0, True, float("nan")])
def test_unusable_values_are_skipped(bad):
    """Strings, negatives and booleans are not costs.

    True is specifically excluded because bool is a subclass of int, so a
    naive isinstance check would record a cost of 1.0.
    """
    r = _response(usage_cost=bad)
    with patch("src.adapters.litellm_adapter.litellm.completion_cost",
               return_value=0.0055):
        cost, source = resolve_cost(r)
    assert cost == pytest.approx(0.0055)
    assert source == "litellm_estimate"


def test_estimate_failure_is_not_fatal():
    """A missing price entry must not take the request down with it."""
    r = _response()
    with patch("src.adapters.litellm_adapter.litellm.completion_cost",
               side_effect=RuntimeError("model not in price map")):
        cost, source = resolve_cost(r)
    assert cost == 0.0
    assert source == "unavailable"
