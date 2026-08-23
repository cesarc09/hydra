"""Hydra status line: model, context bar, split warning, prompt-cache countdown.

Reads Claude Code's status-line payload on stdin. Stdlib only and imported from
nothing - it runs on every assistant message, so `python -m hydra_cli` (95ms of
package import here, against 29ms for bare python) is too heavy a front door.

Two segments beyond the context bar:

  * `312k SPLIT` past 250k input tokens. Cost is the integral of a growing
    context over turns, so splitting the session beats any per-read saving.
  * `⧗ 47m` until the prompt cache expires. Nothing in the status-line payload
    exposes the cache TTL, so it comes from the transcript: the last API
    response's `message.usage.cache_creation` says which bucket was written
    (1h or 5m), and the request that wrote it is the anchor.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

# Flag at an absolute token count, not a fraction: the evidence is absolute
# (sessions past 250k peak carry 89.2% of all cache-read tokens) and the
# fraction moves with the model's window. Only ever fires on a 1M-context
# model - a 200k window auto-compacts long before this.
SPLIT_TOKENS = 250_000
AMBER_MINUTES = 10
TAIL_BYTES = 256 * 1024

RESET = "\033[0m"
RED = "\033[1;31m"
AMBER = "\033[33m"
DIM = "\033[2m"


def _split_threshold() -> int:
    try:
        return int(os.environ.get("HYDRA_STATUSLINE_SPLIT_TOKENS") or SPLIT_TOKENS)
    except ValueError:
        return SPLIT_TOKENS


def _parse_ts(value: Any) -> datetime | None:
    """Transcript timestamps are ISO-8601 UTC with a `Z` suffix."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def tail_records(path: str, limit: int = TAIL_BYTES) -> list[dict[str, Any]]:
    """Parse the last `limit` bytes of a transcript. Transcripts reach tens of
    MB, and only the newest turn is wanted, so this never reads the whole file."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            chunk = fh.read()
    except OSError:
        return []
    if size > limit:
        _, _, chunk = chunk.partition(b"\n")  # drop the truncated first line
    records = []
    for line in chunk.splitlines():
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _ttl_of(usage: dict[str, Any]) -> int | None:
    """TTL minutes implied by the bucket this turn wrote, or None if it wrote none."""
    creation = usage.get("cache_creation")
    if not isinstance(creation, dict):
        return None
    if int(creation.get("ephemeral_1h_input_tokens") or 0) > 0:
        return 60
    if int(creation.get("ephemeral_5m_input_tokens") or 0) > 0:
        return 5
    return None


def cache_state(records: list[dict[str, Any]]) -> tuple[datetime, int] | None:
    """Return (request start, TTL minutes) for the newest API response, or None.

    Anchored on the *request*, not the response: the cache is written when the
    request goes out, so anchoring on the assistant record would over-report the
    remaining time by the whole turn duration (median 19s but minutes on a
    subagent-heavy turn). The user record that triggered the request is the
    closest available proxy for that moment.

    Deliberately not `stat`: metadata-only records (`mode`, `ai-title`,
    `bridge-session`, ...) carry no timestamp and are appended long after the
    last request, so mtime runs days ahead of the transcript's own clock.

    The TTL comes from the newest turn that actually WROTE a bucket, which is
    not always the newest turn: a pure cache-read writes nothing, and assuming
    a default there would report 58m left on a 5m entry with 3m to live. The
    anchor still comes from the newest request, because a read refreshes the
    entry it hit. No write anywhere in the tail means the TTL is unknown, and
    an unknown TTL is reported as no segment rather than as a guess.
    """
    anchor = None
    for index in range(len(records) - 1, -1, -1):
        record = records[index]
        message = record.get("message")
        if record.get("type") != "assistant" or not isinstance(message, dict):
            continue
        usage = message.get("usage")
        started = _parse_ts(record.get("timestamp"))
        if not isinstance(usage, dict) or started is None:
            continue
        if anchor is None:
            for prior in range(index - 1, -1, -1):
                if records[prior].get("type") == "user":
                    triggered = _parse_ts(records[prior].get("timestamp"))
                    if triggered is not None:
                        started = triggered
                    break
            anchor = started
        ttl = _ttl_of(usage)
        if ttl is not None:
            return anchor, ttl
    return None


def _cache_segment(payload: dict[str, Any], now: datetime) -> str | None:
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str) or not transcript:
        return None
    state = cache_state(tail_records(transcript))
    if state is None:
        return None
    started, ttl = state
    remaining = ttl * 60 - (now - started).total_seconds()
    if remaining <= 0:
        return f"{DIM}⧗ cold{RESET}"
    if remaining < 60:
        return f"{AMBER}⧗ <1m{RESET}"
    minutes = int(remaining // 60)  # floor: under-report rather than over-promise
    color = AMBER if minutes < AMBER_MINUTES else DIM
    return f"{color}⧗ {minutes}m{RESET}"


def render(payload: dict[str, Any], now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    model, pct, tokens = "", 0, 0
    entry = payload.get("model")
    if isinstance(entry, dict):
        model = entry.get("display_name") or ""
    window = payload.get("context_window")
    if isinstance(window, dict):
        try:
            pct = int(float(window.get("used_percentage") or 0))
        except (TypeError, ValueError):
            pct = 0
        try:
            tokens = int(window.get("total_input_tokens") or 0)
        except (TypeError, ValueError):
            tokens = 0

    pct = max(0, min(100, pct))
    filled = pct * 10 // 100
    bar = "▓" * filled + "░" * (10 - filled)
    parts = [f"[{model}] {bar} {pct}%"]
    if tokens > _split_threshold():
        parts.append(f"{RED}{tokens // 1000}k SPLIT{RESET}")
    try:
        cache = _cache_segment(payload, now)
    except Exception:
        cache = None
    if cache:
        parts.append(cache)
    return "  ".join(parts)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        print(render(payload))
    except Exception:
        print("[statusline]")


if __name__ == "__main__":
    main()
