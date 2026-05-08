"""Auto-capture Remote Control URL from a Claude Code session transcript.

Wired up as a Stop hook. Scans the transcript JSONL for the most recent
`bridge_status` system event and PUTs its `url` field to Hydra. Empirically,
this event is emitted only by `entrypoint=cli` sessions when the user runs
`/remote-control` — `entrypoint=claude-vscode` never writes it, so this
becomes a silent no-op there and the manual-paste UI takes over.

Failures are swallowed (printed to stderr, exit 0) so a transient network
hiccup or a malformed transcript never breaks the Stop hook chain.
"""
from __future__ import annotations

import argparse
import json
import sys

from hydra_cli import api


def _read_hook_payload() -> dict:
    """Claude Code hooks pipe their event payload as JSON on stdin."""
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


def latest_bridge_url(transcript_path: str) -> str | None:
    """Return the URL from the most recent bridge_status event, or None.

    Filters on the actual JSON shape (type=system, subtype=bridge_status) so
    user/assistant text quoting "bridge_status" doesn't get mistaken for an
    event — the contamination we hit while researching this feature.
    """
    latest: str | None = None
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Cheap pre-filter — most lines won't match.
                if not line or '"bridge_status"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    d.get("type") == "system"
                    and d.get("subtype") == "bridge_status"
                ):
                    url = d.get("url")
                    if isinstance(url, str) and url:
                        latest = url
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return latest


def cmd_capture_remote_url(args: argparse.Namespace) -> None:
    payload = _read_hook_payload()
    session_id = payload.get("session_id") or ""
    transcript_path = payload.get("transcript_path") or ""
    if not session_id or not transcript_path:
        return

    url = latest_bridge_url(transcript_path)
    if not url:
        return

    status, body = api.put_json(
        f"/api/sessions/{session_id}/remote-control-url",
        {"url": url},
    )
    if status not in (200, 204):
        print(
            f"hydra capture-remote-url: PUT failed ({status}): {body}",
            file=sys.stderr,
        )
