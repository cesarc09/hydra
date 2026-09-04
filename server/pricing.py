"""Model rate table for pricing usage_messages rows.

Rates:
- https://platform.claude.com/docs/en/about-claude/pricing
- https://developers.openai.com/api/docs/pricing

Cost is computed HERE, at query time, and never stored on the row - so
correcting a rate retroactively fixes every historical figure the dashboard
shows. `usage_messages` holds only token counts.

An unrecognised model returns None, never 0.0. A silent $0 for a model we
haven't listed is the one failure that would make the whole dashboard quietly
wrong, so unpriced rows are counted and surfaced instead.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class Rate(NamedTuple):
    input: float
    output: float
    cache_read_mult: float = 0.1

# USD per million tokens.
# Only rates we can actually cite live here; anything else is deliberately
# unpriced rather than guessed. Current Claude models serve their 1M context
# at these standard rates, so the `[1m]` suffix needs no dimension of its own.
RATES: dict[str, Rate] = {
    "claude-fable-5-1": Rate(10.0, 50.0, 0.025),
    "claude-mythos-5-1": Rate(10.0, 50.0, 0.025),
    "claude-fable-5": Rate(10.0, 50.0),
    "claude-mythos-5": Rate(10.0, 50.0),
    "claude-opus-5": Rate(5.0, 25.0),
    "claude-opus-4-8": Rate(5.0, 25.0),
    "claude-opus-4-7": Rate(5.0, 25.0),
    "claude-opus-4-6": Rate(5.0, 25.0),
    "claude-opus-4-5": Rate(5.0, 25.0),
    "claude-sonnet-5": Rate(2.0, 10.0),
    "claude-sonnet-4-6": Rate(3.0, 15.0),
    "claude-sonnet-4-5": Rate(3.0, 15.0),
    "claude-haiku-4-5": Rate(1.0, 5.0),
    # Promotional - available at least through November 21, 2026. Re-verify after.
    "gpt-5.6-sol": Rate(4.0, 20.0),
    "gpt-5.6-terra": Rate(2.0, 12.0),
    "gpt-5.6-luna": Rate(0.2, 1.2),
}

# Codex rows never carry write buckets, so these stay Anthropic's.
CACHE_WRITE_5M_MULT = 1.25
CACHE_WRITE_1H_MULT = 2.0

# Server-side web search is billed per request, not per token.
WEB_SEARCH_USD_PER_1K = 10.0

_SUFFIX_RE = re.compile(r"\[[^\]]*\]$")
_DATED_RE = re.compile(r"-\d{8}$")


def normalize_model(model: str) -> str:
    """Reduce a transcript model id to a rate-table key.

    Handles the two shapes Claude Code actually writes: a bare alias
    ("claude-opus-5"), and a dated full id ("claude-haiku-4-5-20251001").
    Also strips a trailing "[1m]"-style routing suffix, which the transcript
    drops but other sources (the statusline payload) carry.
    """
    key = _SUFFIX_RE.sub("", (model or "").strip()).lower()
    if key in RATES:
        return key
    return _DATED_RE.sub("", key)


def rate_for(model: str) -> Rate | None:
    return RATES.get(normalize_model(model))


def cost_components(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_5m_tokens: int = 0,
    cache_write_1h_tokens: int = 0,
    web_search_requests: int = 0,
) -> dict[str, float] | None:
    """Per-component cost for one row or pre-summed group. None if unknown model.

    Split out because "where does the money actually go" is not answerable from
    token counts: cache reads dominate token volume but bill at 0.1x, so the
    shape of the cost is nothing like the shape of the tokens.
    """
    rate = rate_for(model)
    if rate is None:
        return None
    return {
        "input": input_tokens * rate.input / 1_000_000,
        "output": output_tokens * rate.output / 1_000_000,
        "cache_read": cache_read_tokens * rate.input * rate.cache_read_mult / 1_000_000,
        "cache_write_5m": cache_write_5m_tokens * rate.input * CACHE_WRITE_5M_MULT / 1_000_000,
        "cache_write_1h": cache_write_1h_tokens * rate.input * CACHE_WRITE_1H_MULT / 1_000_000,
        "web_search": web_search_requests * WEB_SEARCH_USD_PER_1K / 1000,
    }


def cost_usd(model: str, **counters: int) -> float | None:
    """Price one row (or one pre-summed group). None if the model is unknown."""
    parts = cost_components(model, **counters)
    return None if parts is None else sum(parts.values())
