"""Tests for the Stop-hook auto-capture of Remote Control URLs.

The previous version of this file built every input from a hand-written
`bridge_status` fixture, so it stayed green for weeks after Claude Code 2.1.250
stopped emitting that event and the feature went dead. Everything here is
therefore seeded from REDACTED copies of real transcript lines in
tests/fixtures/ (synthetic ids, real structure), plus a canary that reads the
actual transcript corpus on this machine.
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

from server.services.session_manager import _REMOTE_CONTROL_URL_RE

FIXTURES = Path(__file__).parent / "fixtures"

URL_ALPHA = "https://claude.ai/code/session_FixtureAlpha0123456789AB"
URL_BETA = "https://claude.ai/code/session_FixtureBeta0123456789ABC"
URL_DELTA = "https://claude.ai/code/session_FixtureDelta0123456789AB"


def _fixture(name: str) -> str:
    return str(FIXTURES / name)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _fixture_lines(name: str) -> list[dict]:
    with (FIXTURES / name).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --- url_from_bridge_session_id ---


@pytest.mark.parametrize(
    "bridge_id,expected",
    [
        ("cse_FixtureAlpha0123456789AB", URL_ALPHA),
        ("session_FixtureAlpha0123456789AB", URL_ALPHA),  # already-shimmed id
        ("", None),                                       # disconnect tombstone
        ("cse_", None),                                   # prefix only
        ("brg_FixtureAlpha0123456789AB", None),           # unknown prefix
        ("FixtureAlpha0123456789AB", None),               # no prefix
        ("cse_Fixture-Alpha/0123456789", None),           # non-alphanumeric
        ("cse_FixtureÅlpha0123456789", None),        # non-ASCII
        (None, None),
        (12345, None),
    ],
)
def test_url_from_bridge_session_id(bridge_id: object, expected: str | None):
    assert remote.url_from_bridge_session_id(bridge_id) == expected


# --- scan_bridge_records / latest_bridge_url ---


def test_legacy_bridge_status_alone_still_read():
    """Older CLIs on other machines may not have updated - keep reading shape (a)."""
    assert remote.latest_bridge_url(_fixture("shape_a_bridge_status.jsonl")) == URL_ALPHA


def test_bridge_session_with_and_without_owner_fields():
    """1478 of 1932 real records omit the owner UUIDs - they are not required."""
    path = _fixture("shape_b_bridge_session.jsonl")
    records = _fixture_lines("shape_b_bridge_session.jsonl")
    assert "ownerAccountUuid" in records[0]
    assert "ownerAccountUuid" not in records[1]
    assert remote.latest_bridge_url(path) == URL_BETA


def test_both_shapes_agree():
    """The derivation must reproduce CC's own URL byte for byte."""
    path = _fixture("shape_both_matching_pair.jsonl")
    records = _fixture_lines("shape_both_matching_pair.jsonl")
    legacy = next(r["url"] for r in records if r.get("subtype") == "bridge_status")
    derived = remote.url_from_bridge_session_id(
        next(r["bridgeSessionId"] for r in records if r.get("type") == "bridge-session")
    )
    assert derived == legacy
    assert remote.latest_bridge_url(path) == legacy


def test_tombstone_then_reconnect_returns_new_url():
    scan = remote.scan_bridge_records(_fixture("shape_b_tombstone_synthetic.jsonl"))
    assert scan.url == URL_DELTA
    assert scan.records == 3


def test_connect_then_tombstone_returns_none(tmp_path: Path):
    """A disconnect must not leave a dead URL behind - file order decides."""
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, _fixture_lines("shape_b_tombstone_synthetic.jsonl")[:2])
    scan = remote.scan_bridge_records(str(p))
    assert scan.url is None
    assert scan.records == 2  # records seen, just nothing usable
    assert scan.cleared is True  # a disconnect, distinguishable from drift


def test_malformed_bridge_status_does_not_clear_a_found_url(tmp_path: Path):
    """Only an explicit empty id clears; an unreadable record leaves it alone."""
    p = tmp_path / "t.jsonl"
    records = _fixture_lines("shape_both_matching_pair.jsonl")
    broken = dict(records[1])
    del broken["url"]
    _write_jsonl(p, [records[0], broken])
    assert remote.latest_bridge_url(str(p)) == URL_ALPHA


def test_bridge_status_with_unshimmed_url_is_not_accepted(tmp_path: Path):
    """If the cse-shim flag ever flips, CC writes /code/cse_... - that is drift,
    not a URL, and the server would 400 it."""
    p = tmp_path / "t.jsonl"
    records = _fixture_lines("shape_a_bridge_status.jsonl")
    evt = dict(records[1])
    evt["url"] = "https://claude.ai/code/cse_FixtureAlpha0123456789AB"
    _write_jsonl(p, [evt])
    scan = remote.scan_bridge_records(str(p))
    assert scan.url is None
    assert scan.records == 1
    assert scan.cleared is False  # unreadable, not disconnected


def test_text_quoting_the_record_names_is_ignored(tmp_path: Path):
    """Substring-grep contamination: prose naming these shapes is not a record."""
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [{
        "type": "user",
        "message": {"role": "user", "content": [{
            "type": "text",
            "text": f'I see "bridge-session" and "bridge_status" with {URL_ALPHA}',
        }]},
    }])
    scan = remote.scan_bridge_records(str(p))
    assert scan.url is None
    assert scan.records == 0


def test_missing_file_and_empty_file(tmp_path: Path):
    assert remote.scan_bridge_records(str(tmp_path / "nope.jsonl")) == (None, 0, False)
    p = tmp_path / "e.jsonl"
    p.write_text("", encoding="utf-8")
    assert remote.scan_bridge_records(str(p)) == (None, 0, False)


def test_malformed_lines_are_skipped(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    body = "\n".join([
        "this is not json",
        "",
        '"bridge-session"',  # valid JSON, but a string, not a record
        json.dumps(_fixture_lines("shape_b_bridge_session.jsonl")[0]),
    ])
    p.write_text(body + "\n", encoding="utf-8")
    assert remote.latest_bridge_url(str(p)) == URL_BETA


# --- cmd_capture_remote_url (hook entry point) ---


def _run(payload: str, tmp_path: Path, status: int = 200,
         body: str = "{}") -> tuple[list[Any], str, int]:
    """Run the hook with stdin and the PUT patched. Returns (calls, stderr, exit)."""
    args = argparse.Namespace()
    code = 0
    with mock.patch.object(remote.sys, "stdin", io.StringIO(payload)), \
         mock.patch.object(remote, "_state_dir", lambda: tmp_path / "state"), \
         mock.patch.object(remote.sys, "stderr", io.StringIO()) as err, \
         mock.patch.object(remote.api, "put_json",
                           return_value=(status, body)) as put:
        try:
            remote.cmd_capture_remote_url(args)
        except SystemExit as e:
            code = int(e.code or 0)
        return put.call_args_list, err.getvalue(), code


def _payload(transcript: str, session_id: str = "sess-xyz") -> str:
    return json.dumps({"session_id": session_id, "transcript_path": transcript})


def test_cmd_no_op_on_empty_or_invalid_stdin(tmp_path: Path):
    assert _run("", tmp_path)[0] == []
    assert _run("not json at all", tmp_path)[0] == []
    assert _run(json.dumps({"transcript_path": "/x"}), tmp_path)[0] == []


def test_cmd_puts_url_derived_from_bridge_session(tmp_path: Path):
    calls, err, code = _run(_payload(_fixture("shape_b_bridge_session.jsonl")), tmp_path)
    assert len(calls) == 1
    assert calls[0][0] == ("/api/sessions/sess-xyz/remote-control-url", {"url": URL_BETA})
    assert err == ""
    assert code == 0


def test_cmd_puts_nothing_when_no_bridge_records(tmp_path: Path):
    """THE regression guard: VS Code transcripts carry no bridge records, and a
    PUT of "" here would wipe the URL pasted into the dashboard on every Stop."""
    p = tmp_path / "vscode.jsonl"
    _write_jsonl(p, [{"type": "user", "message": {"role": "user", "content": "hi"}}])
    calls, err, code = _run(_payload(str(p)), tmp_path)
    assert calls == []
    assert err == ""
    assert code == 0


def test_cmd_puts_nothing_on_tombstone(tmp_path: Path):
    """Clearing server-side is deferred; a disconnect must at least not PUT a
    dead URL. SessionEnd already clears it."""
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, _fixture_lines("shape_b_tombstone_synthetic.jsonl")[:2])
    calls, err, code = _run(_payload(str(p)), tmp_path)
    assert calls == []
    # A clean disconnect is not drift: it must stay silent. Discarding these two
    # is what let the false-alert bug through review.
    assert err == ""
    assert code == 0


def test_cmd_reports_shape_drift_once_per_session(tmp_path: Path):
    """Records present but nothing derivable is the failure that went unnoticed;
    it must exit 1 so Claude Code shows a notification - but only the first time."""
    p = tmp_path / "drift.jsonl"
    _write_jsonl(p, [{"type": "bridge-session", "bridgeSessionId": "brg_whatever"}])

    calls, err, code = _run(_payload(str(p)), tmp_path)
    assert calls == []
    assert code == 1
    assert "shape may have changed" in err

    calls, err, code = _run(_payload(str(p)), tmp_path)
    assert code == 0
    assert err == ""


def test_cmd_reports_server_400(tmp_path: Path):
    calls, err, code = _run(
        _payload(_fixture("shape_b_bridge_session.jsonl")), tmp_path,
        status=400, body='{"detail":"bad url"}',
    )
    assert len(calls) == 1
    assert code == 1
    assert "server rejected" in err


@pytest.mark.parametrize("status", [404, 200, 204])
def test_cmd_silent_on_expected_statuses(tmp_path: Path, status: int):
    """404 is the SessionStart race; it is normal and must not alert."""
    _, err, code = _run(
        _payload(_fixture("shape_b_bridge_session.jsonl")), tmp_path, status=status
    )
    assert err == ""
    assert code == 0


def test_cmd_silent_when_offline(tmp_path: Path):
    """api._request does not catch URLError, so an offline machine would
    otherwise raise out of the hook and exit non-zero on every Stop."""
    import urllib.error

    args = argparse.Namespace()
    payload = _payload(_fixture("shape_b_bridge_session.jsonl"))
    with mock.patch.object(remote.sys, "stdin", io.StringIO(payload)), \
         mock.patch.object(remote, "_state_dir", lambda: tmp_path / "state"), \
         mock.patch.object(remote.sys, "stderr", io.StringIO()) as err, \
         mock.patch.object(remote.api, "put_json",
                           side_effect=urllib.error.URLError("offline")):
        remote.cmd_capture_remote_url(args)  # must not raise
    assert err.getvalue() == ""


# --- cross-layer contract ---


def test_every_url_we_build_satisfies_the_server_regex():
    """Client and server must not drift: the server 400s anything else."""
    for bridge_id in (
        "cse_FixtureAlpha0123456789AB",
        "cse_" + "a" * 24,
        "cse_" + "0" * 24,
        "cse_Z9",
        "session_FixtureBeta0123456789ABC",
    ):
        url = remote.url_from_bridge_session_id(bridge_id)
        assert url is not None
        assert _REMOTE_CONTROL_URL_RE.match(url), url

    for path in sorted(FIXTURES.glob("*.jsonl")):
        url = remote.latest_bridge_url(str(path))
        if url is not None:
            assert _REMOTE_CONTROL_URL_RE.match(url), path.name


# --- corpus canary ---

CORPUS = Path.home() / ".claude" / "projects"
CANARY_FILES = 40


def _used_remote_control(path: Path) -> bool:
    """Detect Remote Control use WITHOUT the parser under test.

    Deliberately duplicates the shape check with its own literal strings. If the
    canary asked the parser for the denominator, a parser that went blind would
    report no records and the test would pass vacuously - which is exactly how
    the original breakage survived 13 green tests.
    """
    active = False
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"bridgeSessionId"' not in line and '"bridge_status"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(d, dict):
                    continue
                if d.get("type") == "bridge-session":
                    # Last state wins: a transcript ending in a disconnect has
                    # no current URL, and the parser is right to return None.
                    active = bool(d.get("bridgeSessionId"))
                elif d.get("subtype") == "bridge_status" and d.get("url"):
                    active = True
    except OSError:
        return False
    return active


def test_recent_real_transcripts_still_yield_a_url():
    """The test that would have caught this. Reads the newest real transcripts on
    this machine and asserts the parser still finds a URL in every one that an
    independent check says used Remote Control.

    Failure output names counts and filenames only - never session ids or owner
    UUIDs, which would leak into CI logs.
    """
    if not CORPUS.is_dir():
        pytest.skip(f"no transcript corpus at {CORPUS}")
    files = sorted(CORPUS.glob("*/*.jsonl"), key=lambda f: f.stat().st_mtime,
                   reverse=True)[:CANARY_FILES]
    if not files:
        pytest.skip("transcript corpus is empty")

    rc_files = [f for f in files if _used_remote_control(f)]
    if not rc_files:
        pytest.skip(
            f"none of the {len(files)} newest transcripts used Remote Control"
        )

    missing = 0
    for f in rc_files:
        url = remote.latest_bridge_url(str(f))
        if url is None:
            missing += 1
        else:
            assert _REMOTE_CONTROL_URL_RE.match(url), "malformed URL derived"

    assert not missing, (
        f"{missing} of {len(rc_files)} recent Remote Control transcripts yielded"
        " no URL - the transcript shape has probably changed. Run"
        " `python -m hydra_cli doctor`."
    )
