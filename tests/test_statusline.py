"""Status-line rendering: context bar, 250k split flag, prompt-cache countdown."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# hydra_statusline.py ships beside setup.sh, not inside the hydra_cli package: it
# runs on every assistant message and must not pay the package import.
_SPEC = importlib.util.spec_from_file_location(
    "hydra_statusline",
    Path(__file__).resolve().parents[1] / "client" / "hydra_statusline.py",
)
assert _SPEC and _SPEC.loader
statusline = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(statusline)

NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)


def _payload(**kwargs):
    base = {
        "model": {"display_name": "Opus"},
        "context_window": {"used_percentage": 32, "total_input_tokens": 64_000},
    }
    base.update(kwargs)
    return base


def _transcript(tmp_path: Path, records: list[dict]) -> str:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(path)


def _turn(
    minutes_ago: float,
    *,
    ttl_1h: int = 4096,
    ttl_5m: int = 0,
    cache_read: int = 0,
    gap_s: int = 20,
):
    started = NOW - timedelta(minutes=minutes_ago)
    return [
        {"type": "user", "timestamp": started.isoformat().replace("+00:00", "Z")},
        {
            "type": "assistant",
            "timestamp": (started + timedelta(seconds=gap_s))
            .isoformat()
            .replace("+00:00", "Z"),
            "message": {
                "id": "msg_1",
                "usage": {
                    "cache_read_input_tokens": cache_read,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": ttl_1h,
                        "ephemeral_5m_input_tokens": ttl_5m,
                    },
                },
            },
        },
    ]


def test_bar_and_model_only_by_default():
    assert statusline.render(_payload(), now=NOW) == "[Opus] ▓▓▓░░░░░░░ 32%"


def test_split_flag_past_threshold():
    out = statusline.render(
        _payload(context_window={"used_percentage": 62, "total_input_tokens": 312_345}),
        now=NOW,
    )
    assert "312k SPLIT" in out
    assert statusline.RED in out


@pytest.mark.parametrize("tokens", [0, 250_000])
def test_no_split_flag_at_or_below_threshold(tokens):
    """0 is the pre-first-API-call value, not a small context."""
    payload = _payload(context_window={"used_percentage": 0, "total_input_tokens": tokens})
    assert "SPLIT" not in statusline.render(payload, now=NOW)


def test_split_threshold_env_override(monkeypatch):
    monkeypatch.setenv("HYDRA_STATUSLINE_SPLIT_TOKENS", "50000")
    assert "64k SPLIT" in statusline.render(_payload(), now=NOW)


def test_cache_countdown_anchors_on_the_request_not_the_response(tmp_path):
    """The cache is written when the request goes out, so a slow turn must not
    buy back its own duration - a 12-minute turn started 20 minutes ago leaves
    40 minutes, not 52."""
    path = _transcript(tmp_path, _turn(20, gap_s=12 * 60))
    out = statusline.render(_payload(transcript_path=path), now=NOW)
    assert "⧗ 40m" in out


def test_cache_countdown_is_amber_near_expiry(tmp_path):
    path = _transcript(tmp_path, _turn(54, gap_s=0))
    out = statusline.render(_payload(transcript_path=path), now=NOW)
    assert "⧗ 6m" in out
    assert statusline.AMBER in out


def test_cache_reads_cold_past_the_ttl(tmp_path):
    path = _transcript(tmp_path, _turn(72, gap_s=0))
    out = statusline.render(_payload(transcript_path=path), now=NOW)
    assert "⧗ cold" in out


def test_five_minute_bucket_shortens_the_window(tmp_path):
    """An overage session drops to 5m writes; counting down from 60 would show
    47 minutes on a cache that died 3 minutes ago."""
    path = _transcript(tmp_path, _turn(8, ttl_1h=0, ttl_5m=4096, gap_s=0))
    assert "⧗ cold" in statusline.render(_payload(transcript_path=path), now=NOW)


def test_a_write_less_turn_keeps_the_last_observed_bucket(tmp_path):
    """A pure cache-read writes nothing. Assuming a default there would report
    58m left on a 5m entry with ~3m to live, so the TTL comes from the newest
    turn that actually wrote one - while the anchor stays the newest request,
    because the read refreshed the entry it hit."""
    records = [
        *_turn(9, ttl_1h=0, ttl_5m=4096, gap_s=0),
        *_turn(2, ttl_1h=0, ttl_5m=0, cache_read=140_000, gap_s=0),
    ]
    path = _transcript(tmp_path, records)
    assert "⧗ 3m" in statusline.render(_payload(transcript_path=path), now=NOW)


def test_unknown_ttl_shows_no_segment(tmp_path):
    """No bucket anywhere in the tail: the TTL is unknown, and unknown is
    reported as silence rather than as a guess."""
    path = _transcript(
        tmp_path, _turn(10, ttl_1h=0, ttl_5m=0, cache_read=140_000, gap_s=0)
    )
    out = statusline.render(_payload(transcript_path=path), now=NOW)
    assert "⧗" not in out
    assert out == "[Opus] ▓▓▓░░░░░░░ 32%"


def test_trailing_metadata_records_do_not_move_the_anchor(tmp_path):
    """`mode`, `ai-title` and friends are appended with no timestamp long after
    the last request - the reason mtime is unusable here."""
    records = [
        *_turn(20, gap_s=0),
        {"type": "mode", "mode": "default"},
        {"type": "ai-title", "title": "something"},
    ]
    path = _transcript(tmp_path, records)
    assert "⧗ 40m" in statusline.render(_payload(transcript_path=path), now=NOW)


def test_newest_turn_wins(tmp_path):
    path = _transcript(tmp_path, _turn(50, gap_s=0) + _turn(10, gap_s=0))
    assert "⧗ 50m" in statusline.render(_payload(transcript_path=path), now=NOW)


def test_tail_read_drops_the_truncated_first_line(tmp_path):
    path = Path(_transcript(tmp_path, _turn(10, gap_s=0)))
    records = statusline.tail_records(str(path), limit=path.stat().st_size - 10)
    assert all(isinstance(r, dict) for r in records)
    assert records[-1]["type"] == "assistant"


def test_missing_transcript_degrades_to_no_segment(tmp_path):
    out = statusline.render(_payload(transcript_path=str(tmp_path / "gone.jsonl")), now=NOW)
    assert out == "[Opus] ▓▓▓░░░░░░░ 32%"


def test_malformed_payload_still_renders():
    assert statusline.render({}, now=NOW).startswith("[] ")
    assert statusline.render(
        {"model": "opus", "context_window": {"used_percentage": "x"}}, now=NOW
    ).endswith("0%")
