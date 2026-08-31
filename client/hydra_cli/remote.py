"""Auto-capture the Remote Control URL from a Claude Code session transcript.

Wired up as a Stop hook. Claude Code records the bridge in two shapes: a legacy
`bridge_status` system event carrying CC's own URL (seen 2.1.118 - 2.1.240),
and a `bridge-session` record carrying only a `cse_` id (2.1.142 onward, and
the ONLY shape since 2.1.250, when Remote Control became on by default and the
legacy event stopped being written). We scan in file order and let the last
bridge record win, whichever shape it is, so a reconnect supersedes and a
disconnect does not resurrect a dead URL.

Deriving the URL from the id mirrors CC's own `toCompatSessionId`: swap the
`cse_` prefix for `session_`. Verified against 1825 records across the 64
transcripts carrying both shapes - 0 mismatches. CC gates that swap on the
server-side flag `tengu_bridge_repl_v2_cse_shim_enabled` (default on); if it is
ever turned off CC builds `/code/cse_...` and our derived URL is silently
wrong, with no local signal - which is why `hydra doctor` reports the CC
version, so a future drift report can name it.

Exit codes are the alerting channel: a transcript that HAS bridge records but
yields no usable URL, or a server 400, exits 1 (Claude Code shows a
non-blocking "Stop hook error" notification), at most once per session.
Everything else exits 0 - offline machines, missing transcripts, and sessions
with no bridge records at all (VS Code never writes them) are all normal.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from pathlib import Path
from typing import NamedTuple

from hydra_cli import api

# Cheap per-line pre-filter; both shapes must be admitted.
_BRIDGE_MARKERS = ('"bridge-session"', '"bridge_status"')

# Mirrors _REMOTE_CONTROL_URL_RE in server/services/session_manager.py - the
# server 400s anything else, so we validate before PUTting rather than after.
_URL_BASE = "https://claude.ai/code/session_"

# One marker per session, written only when a failure is actually reported, so
# a healthy machine never creates this directory at all.
_STATE_DIRNAME = ".hydra-remote"


class BridgeScan(NamedTuple):
    """Outcome of one transcript scan.

    Three outcomes, never two. `records` separates "nothing to capture" from
    "cannot parse what is there": a session with no bridge records must never
    touch the stored URL, or every VS Code Stop would wipe its own
    manually-pasted link. `cleared` then separates a clean disconnect from
    genuine drift, so an orderly shutdown is not reported as a broken parser.
    """

    url: str | None
    records: int
    cleared: bool = False


def read_hook_payload() -> dict:
    """Claude Code hooks pipe their event payload as JSON on stdin.

    Public because every command-type hook entry point needs it (`usage report`
    as well as this module); returns {} rather than raising so a hook never dies
    on a malformed or absent payload.
    """
    if sys.stdin.isatty():
        return {}
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def is_valid_remote_control_url(url: str) -> bool:
    """Shape the server enforces. Public so tests can assert the contract."""
    if not url.startswith(_URL_BASE):
        return False
    ident = url[len(_URL_BASE):]
    return bool(ident) and ident.isascii() and ident.isalnum()


def url_from_bridge_session_id(bridge_session_id: object) -> str | None:
    """Derive the Remote Control URL from a bridge-session id, or None.

    None for the disconnect tombstone (empty id), an unknown prefix, or an id
    we cannot validate - we never build something the server would reject.
    """
    if not isinstance(bridge_session_id, str):
        return None
    for prefix in ("cse_", "session_"):
        if bridge_session_id.startswith(prefix):
            ident = bridge_session_id[len(prefix):]
            break
    else:
        return None
    if not ident or not ident.isascii() or not ident.isalnum():
        return None
    return _URL_BASE + ident


def scan_bridge_records(transcript_path: str) -> BridgeScan:
    """Single pass over the transcript; the last bridge record wins.

    Matches on the JSON shape, never a substring: user/assistant text quoting
    these names would otherwise contaminate the scan. A record we cannot read
    leaves the running URL alone; only an explicit empty id clears it.
    """
    url: str | None = None
    records = 0
    cleared = False
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or not any(m in line for m in _BRIDGE_MARKERS):
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(d, dict):
                    continue
                if d.get("type") == "bridge-session":
                    records += 1
                    bid = d.get("bridgeSessionId")
                    # An empty id is the disconnect tombstone and clears; an
                    # unreadable one leaves the running value alone.
                    if bid == "":
                        url, cleared = None, True
                    elif (derived := url_from_bridge_session_id(bid)) is not None:
                        url, cleared = derived, False
                elif d.get("type") == "system" and d.get("subtype") == "bridge_status":
                    records += 1
                    raw = d.get("url")
                    # Validated, not trusted: a flipped cse-shim flag would put
                    # a /code/cse_... URL here, and that is drift, not a URL.
                    if isinstance(raw, str) and is_valid_remote_control_url(raw):
                        url, cleared = raw, False
    except OSError:
        return BridgeScan(None, 0)
    return BridgeScan(url, records, cleared)


def latest_bridge_url(transcript_path: str) -> str | None:
    """URL-only view of the scan, for `hydra doctor` and callers that only care."""
    return scan_bridge_records(transcript_path).url


def _state_dir() -> Path:
    return Path.home() / ".claude" / _STATE_DIRNAME


def _report_once(session_id: str, message: str) -> bool:
    """Print a failure to stderr at most once per session.

    Returns True when it printed, so the caller can exit 1 and let Claude Code
    raise its non-blocking notification. If the marker cannot be written we
    report anyway - losing the alert is worse than repeating it.
    """
    marker = _state_dir() / f"{session_id.replace('/', '_')}.warned"
    try:
        if marker.exists():
            return False
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        pass
    print(f"hydra capture-remote-url: {message}", file=sys.stderr)
    return True


def cmd_capture_remote_url(args: argparse.Namespace) -> None:
    payload = read_hook_payload()
    session_id = payload.get("session_id") or ""
    transcript_path = payload.get("transcript_path") or ""
    if not session_id or not transcript_path:
        return

    scan = scan_bridge_records(transcript_path)
    if not scan.records:
        # Nothing to capture: VS Code, or Remote Control never connected. PUT
        # nothing - an empty body here would clear a manually-pasted URL.
        return
    if scan.url is None:
        if scan.cleared:
            # Orderly disconnect, not drift. Clearing server-side is deferred;
            # SessionEnd already does it.
            return
        if _report_once(
            session_id,
            "transcript has bridge records but no usable URL - the transcript"
            " shape may have changed; run `python -m hydra_cli doctor`",
        ):
            sys.exit(1)
        return

    try:
        status, body = api.put_json(
            f"/api/sessions/{session_id}/remote-control-url",
            {"url": scan.url},
        )
    except urllib.error.URLError:
        return  # offline machine: normal, never alert on it
    if status == 400 and _report_once(
        session_id,
        f"server rejected the URL we built ({body.strip()}) - run"
        " `python -m hydra_cli doctor`",
    ):
        sys.exit(1)
    if status not in (200, 204, 400, 404):
        # 404 is the SessionStart race and is expected; the rest is worth a line
        # in the transcript but is not shape drift, so it does not exit 1.
        print(
            f"hydra capture-remote-url: PUT failed ({status}): {body}",
            file=sys.stderr,
        )
