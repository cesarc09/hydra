"""Tests for the Stop-hook auto-capture of Remote Control URLs.

Covers `latest_bridge_url` (transcript scanning) plus the dispatch wrapper
(`cmd_capture_remote_url`) end-to-end with `api.put_json` patched. Skips
network: we test that the right call would be made, not that it succeeds.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from hydra_cli import remote

URL_A = "https://claude.ai/code/session_01PwcGt9jEjKXeJpUzfoe"
URL_B = "https://claude.ai/code/session_01KZNTinQNtYJMMXoC7Ymcf1"


def _bridge_event(url: str, ts: str = "2026-05-08T09:00:00Z") -> dict:
    return {
        "type": "system",
        "subtype": "bridge_status",
        "url": url,
        "content": f"/remote-control is active. Code in CLI or at {url}",
        "isMeta": False,
        "timestamp": ts,
        "entrypoint": "cli",
        "version": "2.1.133",
    }


def _user_text_event(text: str) -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "timestamp": "2026-05-08T09:00:00Z",
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# --- latest_bridge_url ---


def test_latest_bridge_url_returns_none_for_missing_file(tmp_path: Path):
    assert remote.latest_bridge_url(str(tmp_path / "does-not-exist.jsonl")) is None


def test_latest_bridge_url_returns_none_for_empty_file(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    p.write_text("", encoding="utf-8")
    assert remote.latest_bridge_url(str(p)) is None


def test_latest_bridge_url_returns_none_when_no_bridge_status(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [_user_text_event("hello"), _user_text_event("world")])
    assert remote.latest_bridge_url(str(p)) is None


def test_latest_bridge_url_returns_url_when_present(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [_user_text_event("hi"), _bridge_event(URL_A)])
    assert remote.latest_bridge_url(str(p)) == URL_A


def test_latest_bridge_url_returns_most_recent_when_multiple(tmp_path: Path):
    """If /remote-control was run twice in a session (e.g. after a network
    outage), the URL changes. We want the most recent one."""
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            _bridge_event(URL_A, ts="2026-05-08T09:00:00Z"),
            _user_text_event("some user message"),
            _bridge_event(URL_B, ts="2026-05-08T10:00:00Z"),
        ],
    )
    assert remote.latest_bridge_url(str(p)) == URL_B


def test_latest_bridge_url_ignores_text_quoting_bridge_status(tmp_path: Path):
    """Substring-grep contamination case: a user/assistant message that
    quotes "bridge_status" or contains an RC URL must NOT be treated as the
    event. This actually happened during research."""
    p = tmp_path / "t.jsonl"
    contamination = _user_text_event(
        f'I see "bridge_status" events with url={URL_A} '
        'in the transcript. Type system, subtype bridge_status.'
    )
    _write_jsonl(p, [contamination])
    assert remote.latest_bridge_url(str(p)) is None


def test_latest_bridge_url_skips_malformed_lines(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    p.write_text(
        json.dumps(_user_text_event("ok")) + "\n"
        + "this is not json\n"
        + "\n"  # blank line
        + json.dumps(_bridge_event(URL_A)) + "\n",
        encoding="utf-8",
    )
    assert remote.latest_bridge_url(str(p)) == URL_A


def test_latest_bridge_url_ignores_event_without_url_field(tmp_path: Path):
    """Defensive: if a future schema change removes `url`, don't crash."""
    p = tmp_path / "t.jsonl"
    evt = _bridge_event(URL_A)
    del evt["url"]
    _write_jsonl(p, [evt])
    assert remote.latest_bridge_url(str(p)) is None


# --- cmd_capture_remote_url (hook entry point) ---


def _run_with_stdin(stdin_payload: str) -> list[Any]:
    """Run cmd_capture_remote_url with given stdin and return PUT call args."""
    args = argparse.Namespace()
    with mock.patch.object(remote.sys, "stdin", io.StringIO(stdin_payload)), \
         mock.patch.object(remote.api, "put_json", return_value=(200, "{}")) as put:
        remote.cmd_capture_remote_url(args)
    return put.call_args_list


def test_cmd_no_op_when_stdin_empty():
    calls = _run_with_stdin("")
    assert calls == []


def test_cmd_no_op_when_stdin_invalid_json():
    calls = _run_with_stdin("not json at all")
    assert calls == []


def test_cmd_no_op_when_payload_missing_session_id(tmp_path: Path):
    payload = json.dumps({"transcript_path": str(tmp_path / "t.jsonl")})
    calls = _run_with_stdin(payload)
    assert calls == []


def test_cmd_no_op_when_transcript_missing(tmp_path: Path):
    """File doesn't exist → silently no-op (not an error)."""
    payload = json.dumps({
        "session_id": "abc",
        "transcript_path": str(tmp_path / "missing.jsonl"),
    })
    calls = _run_with_stdin(payload)
    assert calls == []


def test_cmd_no_op_when_transcript_has_no_bridge_status(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [_user_text_event("nothing relevant")])
    payload = json.dumps({
        "session_id": "abc",
        "transcript_path": str(p),
    })
    calls = _run_with_stdin(payload)
    assert calls == []


def test_cmd_puts_url_when_bridge_status_present(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [_bridge_event(URL_A)])
    payload = json.dumps({
        "session_id": "sess-xyz",
        "transcript_path": str(p),
    })
    calls = _run_with_stdin(payload)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "/api/sessions/sess-xyz/remote-control-url"
    assert args[1] == {"url": URL_A}


def test_cmd_does_not_raise_on_put_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """A failed PUT must not break the Stop hook chain."""
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [_bridge_event(URL_A)])
    payload = json.dumps({
        "session_id": "sess-xyz",
        "transcript_path": str(p),
    })
    args = argparse.Namespace()
    with mock.patch.object(remote.sys, "stdin", io.StringIO(payload)), \
         mock.patch.object(
             remote.api, "put_json", return_value=(500, "internal error")
         ):
        remote.cmd_capture_remote_url(args)  # must not raise
    err = capsys.readouterr().err
    assert "PUT failed" in err
