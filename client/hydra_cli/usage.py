"""Report per-message token usage from Claude Code transcripts to Hydra.

Wired up as a Stop and SessionEnd hook. Four properties of the transcript
format drive everything here, all measured rather than assumed:

1. One API message writes N assistant records, each repeating the SAME usage
   block (480 records for 234 messages in one session - 2.55x inflation if
   summed naively). Rows are keyed on `message.id`, first seen wins.
2. Subagent usage is NOT in the main transcript. It lives in
   `<dir>/<session_id>/subagents/**/*.jsonl`, and each of those records is
   self-describing (`attributionAgent` carries the agent type), so no
   cross-file map is needed.
3. `message.id` repeats ACROSS transcript files - a resume or fork copies prior
   history into a new session file under a new session_id.
4. `toolUseResult.totalTokens` looks like a run total and is not (it is the
   final message's counters summed), so it is ignored entirely.

Identity is the server's `message_id` primary key, and ingest is
INSERT OR IGNORE. The byte offsets this module keeps are a bandwidth
optimisation ONLY: they may be wrong in either direction - lost, stale, or
never written - without corrupting the data, because the server drops anything
it has already stored. That is what makes (3) a non-event and what makes a
failed POST safe to simply not acknowledge.

Failures print to stderr and return non-zero without advancing any offset, so
the same rows are retried on the next Stop.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from hydra_cli import api
from hydra_cli.remote import read_hook_payload

# Server caps the body at 1 MB; 500 messages is ~125 KB of JSON.
CHUNK = 500

_STATE_DIRNAME = ".hydra-usage"


def state_dir() -> Path:
    """One state file per session, NOT one shared file: several sessions run at
    once and their Stop hooks fire independently, so a shared file would be a
    read-modify-write race between them."""
    return Path.home() / ".claude" / _STATE_DIRNAME


def _state_path(session_id: str) -> Path:
    return state_dir() / f"{session_id}.json"


def _load_offsets(session_id: str) -> dict[str, int]:
    try:
        data = json.loads(_state_path(session_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    files = data.get("files", {}) if isinstance(data, dict) else {}
    return {k: v for k, v in files.items() if isinstance(v, int)}


def _save_offsets(session_id: str, offsets: dict[str, int]) -> None:
    path = _state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"files": offsets}, indent=2) + "\n", encoding="utf-8"
    )


def transcript_files(transcript_path: str, session_id: str) -> list[str]:
    """The main transcript plus every subagent transcript this session OWNS.

    `rglob` does not descend into symlinked directories, and that is deliberate:
    Claude Code aliases a workflow's subagent directory into other sessions that
    consume it (`A/subagents/workflows/wf_x -> B/subagents/workflows/wf_x`).
    Following those links would report one workflow's agents under whichever
    session happened to scan first. Measured over the whole corpus, every real
    subagent file is reachable without following a single symlink, so skipping
    them costs no data and attributes each agent to the session that owns it.
    """
    main = Path(transcript_path)
    files: list[str] = []
    if main.is_file():
        files.append(str(main))
    subagents = main.parent / session_id / "subagents"
    if subagents.is_dir():
        files.extend(sorted(str(p) for p in subagents.rglob("*.jsonl")))
    return files


def _row_from_line(raw: bytes) -> dict[str, Any] | None:
    """Parse one transcript line into a usage row, or None if it carries none."""
    if b'"assistant"' not in raw:  # cheap pre-filter; most lines are not usage
        return None
    try:
        rec = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None
    if not isinstance(rec, dict) or rec.get("type") != "assistant":
        return None

    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    message_id = msg.get("id")
    model = msg.get("model")
    ts = rec.get("timestamp")
    usage = msg.get("usage")
    # `<synthetic>` is the placeholder on API-error records - no real spend.
    # A row with no timestamp cannot be placed on a day, so it is dropped too.
    if not message_id or not model or model == "<synthetic>" or not ts:
        return None
    if not isinstance(usage, dict):
        return None

    # The TTL split decides the price of a cache write (1.25x vs 2x base input),
    # so the two buckets are carried separately. They disagree with the reported
    # total in 15 of 41k measured records, in both directions:
    #  - buckets non-zero, total 0: the buckets match the per-iteration figures,
    #    so the buckets win and the total is simply ignored;
    #  - total non-zero, buckets both 0: the split was dropped, so the shortfall
    #    is attributed to the cheaper 5m bucket rather than silently lost.
    # A record with no `cache_creation` at all is the same second case.
    reported = int(usage.get("cache_creation_input_tokens") or 0)
    creation = usage.get("cache_creation")
    if isinstance(creation, dict):
        write_5m = int(creation.get("ephemeral_5m_input_tokens") or 0)
        write_1h = int(creation.get("ephemeral_1h_input_tokens") or 0)
    else:
        write_5m, write_1h = 0, 0
    shortfall = reported - (write_5m + write_1h)
    if shortfall > 0:
        write_5m += shortfall

    server_tools = usage.get("server_tool_use")
    if not isinstance(server_tools, dict):
        server_tools = {}

    return {
        "message_id": message_id,
        "ts": ts,
        "model": model,
        "cwd": rec.get("cwd"),
        "effort": rec.get("effort"),
        "is_subagent": bool(rec.get("isSidechain")),
        "agent_type": rec.get("attributionAgent"),
        "service_tier": usage.get("service_tier"),
        "speed": usage.get("speed"),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "cache_write_5m_tokens": write_5m,
        "cache_write_1h_tokens": write_1h,
        "web_search_requests": int(server_tools.get("web_search_requests") or 0),
        "web_fetch_requests": int(server_tools.get("web_fetch_requests") or 0),
    }


def parse_file(path: str, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Parse usage rows appended to `path` after `offset`.

    Returns (rows, new_offset). Transcripts are append-only, so a byte offset is
    a valid resume point. Two rules keep it honest:
    - a trailing PARTIAL line (Claude Code mid-write) is not consumed, so the
      offset never lands inside a JSON object;
    - a file smaller than the recorded offset was truncated or replaced, so the
      offset resets to 0 and the whole file is re-read.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    pos = 0 if size < offset else offset

    rows: list[dict[str, Any]] = []
    try:
        with open(path, "rb") as handle:
            handle.seek(pos)
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break
                pos += len(raw)
                row = _row_from_line(raw)
                if row is not None:
                    rows.append(row)
    except OSError as exc:
        print(f"hydra usage: cannot read {path}: {exc}", file=sys.stderr)
        return [], offset
    return rows, pos


def report_session(session_id: str, transcript_path: str, *, quiet: bool = True) -> int:
    """Send this session's unreported usage. Returns 0 on success, 1 on error."""
    offsets = _load_offsets(session_id)
    pending: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for path in transcript_files(transcript_path, session_id):
        new_rows, new_offset = parse_file(path, offsets.get(path, 0))
        pending[path] = new_offset
        for row in new_rows:
            if row["message_id"] in seen:
                continue
            seen.add(row["message_id"])
            rows.append(row)

    for start in range(0, len(rows), CHUNK):
        chunk = rows[start : start + CHUNK]
        status, body = api.post(
            "/api/usage/messages", {"session_id": session_id, "messages": chunk}
        )
        if status not in (200, 204):
            # Offsets are NOT saved, so these rows go again on the next Stop.
            print(f"hydra usage: POST failed ({status}): {body}", file=sys.stderr)
            return 1

    _save_offsets(session_id, pending)
    if rows and not quiet:
        print(f"  usage: {len(rows)} messages reported for {session_id}")
    return 0


def cmd_report(_args: Any) -> int:
    """Stop / SessionEnd hook entry point."""
    payload = read_hook_payload()
    session_id = payload.get("session_id") or ""
    transcript_path = payload.get("transcript_path") or ""
    if not session_id or not transcript_path:
        return 0
    return report_session(session_id, transcript_path)


def run_backfill(root: str | None = None) -> int:
    """Import every transcript on this machine.

    Safe to re-run: it writes the same per-session offsets, and the server
    ignores message ids it already holds regardless.
    """
    base = Path(root) if root else Path.home() / ".claude" / "projects"
    if not base.is_dir():
        print(f"hydra usage: no transcript root at {base}", file=sys.stderr)
        return 1

    sessions = sorted(base.glob("*/*.jsonl"))
    failures = 0
    for main in sessions:
        if report_session(main.stem, str(main), quiet=True) != 0:
            failures += 1
    print(
        f"  usage backfill: {len(sessions) - failures}/{len(sessions)} sessions"
        f" imported from {base}"
    )
    return 1 if failures else 0
