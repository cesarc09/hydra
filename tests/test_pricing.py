"""Per-model usage pricing."""

import pytest

from server import pricing


def test_fable_5_1_cache_reads_use_reduced_multiplier():
    parts = pricing.cost_components("claude-fable-5-1", cache_read_tokens=1_000_000)

    assert parts is not None
    assert parts["cache_read"] == pytest.approx(0.25)


def test_sonnet_5_uses_permanent_rates():
    parts = pricing.cost_components(
        "claude-sonnet-5", input_tokens=1_000_000, output_tokens=1_000_000
    )

    assert parts is not None
    assert parts["input"] == pytest.approx(2.0)
    assert parts["output"] == pytest.approx(10.0)


def test_sol_cache_reads_use_standard_multiplier():
    parts = pricing.cost_components("gpt-5.6-sol", cache_read_tokens=1_000_000)

    assert parts is not None
    assert parts["cache_read"] == pytest.approx(0.4)


def test_unknown_model_stays_unpriced():
    assert pricing.rate_for("model-from-the-future") is None
    assert pricing.cost_components("model-from-the-future") is None


def test_model_suffix_normalisation_is_unchanged():
    assert pricing.rate_for("claude-fable-5-1[1m]") == pricing.RATES["claude-fable-5-1"]
    assert pricing.rate_for("claude-haiku-4-5-20251001") == pricing.RATES["claude-haiku-4-5"]
