from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime

from server.db import get_db
from server.models import HookEvent

# SSE subscribers: list of asyncio.Queue that receive new events
_subscribers: list[asyncio.Queue] = []


_SUBSCRIBER_QUEUE_MAX = 1000


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAX)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue):
    with contextlib.suppress(ValueError):
        _subscribers.remove(q)


async def _broadcast(data: dict):
    # Iterate over a copy so we can drop slow subscribers mid-broadcast.
    for q in list(_subscribers):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            unsubscribe(q)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _summarize_tool_input(event: HookEvent) -> str | None:
    if not event.tool_input:
        return None
    # For file operations, show the file path
    if fp := event.tool_input.get("file_path"):
        return fp
    # For bash, show the command (truncated)
    if cmd := event.tool_input.get("command"):
        return cmd[:120]
    # For grep/glob, show the pattern
    if pat := event.tool_input.get("pattern"):
        return pat[:80]
    return None


async def handle_event(event: HookEvent, instance_id: str):
    db = await get_db()
    now = _now()
    summary = _summarize_tool_input(event)

    # Ensure session row exists (handles missed SessionStart)
    await db.execute(
        """INSERT INTO sessions
           (session_id, instance_id, status, cwd, model,
            started_at, last_event_at, files_changed)
           VALUES (?, ?, 'active', ?, ?, ?, ?, '[]')
           ON CONFLICT(session_id) DO NOTHING""",
        (event.session_id, instance_id, event.cwd, event.model, now, now),
    )

    match event.hook_event_name:
        case "SessionStart":
            # Upsert session
            await db.execute(
                """INSERT INTO sessions
                   (session_id, instance_id, status, cwd, model,
                    started_at, last_event_at, files_changed)
                   VALUES (?, ?, 'active', ?, ?, ?, ?, '[]')
                   ON CONFLICT(session_id) DO UPDATE SET
                     status='active', cwd=?, model=?, last_event_at=?""",
                (event.session_id, instance_id, event.cwd, event.model,
                 now, now,
                 event.cwd, event.model, now),
            )

        case "SessionEnd":
            await db.execute(
                "UPDATE sessions SET status='ended', last_event_at=?,"
                " end_reason=? WHERE session_id=?",
                (now, event.source, event.session_id),
            )

        case "UserPromptSubmit":
            await db.execute(
                "UPDATE sessions SET status='active', last_event_at=? WHERE session_id=?",
                (now, event.session_id),
            )

        case "PostToolUse":
            updates = {"last_event_at": now, "status": "active"}
            if event.tool_name:
                updates["last_tool"] = event.tool_name
            if summary:
                updates["last_tool_input_summary"] = summary

            # Track files changed for Write/Edit
            if event.tool_name in ("Write", "Edit") and event.tool_input:
                fp = event.tool_input.get("file_path", "")
                if fp:
                    rows = list(await db.execute_fetchall(
                        "SELECT files_changed FROM sessions WHERE session_id=?",
                        (event.session_id,),
                    ))
                    if rows:
                        files = json.loads(rows[0][0] or "[]")
                        if fp not in files:
                            files.append(fp)
                            updates["files_changed"] = json.dumps(files)

            set_clause = ", ".join(f"{k}=?" for k in updates)
            values = [*updates.values(), event.session_id]
            await db.execute(
                f"UPDATE sessions SET {set_clause} WHERE session_id=?",
                values,
            )

        case "Stop":
            await db.execute(
                "UPDATE sessions SET status='idle', last_event_at=? WHERE session_id=?",
                (now, event.session_id),
            )

        case "Notification":
            if event.notification_type == "idle_prompt":
                await db.execute(
                    "UPDATE sessions SET status='waiting_input',"
                    " last_event_at=? WHERE session_id=?",
                    (now, event.session_id),
                )

        case "SubagentStart" | "SubagentStop":
            await db.execute(
                "UPDATE sessions SET last_event_at=?, last_tool=? WHERE session_id=?",
                (now, f"Agent({event.agent_type or '?'})", event.session_id),
            )

    # Record the event
    await db.execute(
        """INSERT INTO events
           (session_id, instance_id, event_name, tool_name,
            tool_input_summary, received_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (event.session_id, instance_id, event.hook_event_name,
         event.tool_name, summary, now),
    )
    await db.commit()

    # Broadcast to SSE subscribers
    await _broadcast({
        "session_id": event.session_id,
        "instance_id": instance_id,
        "event_name": event.hook_event_name,
        "tool_name": event.tool_name,
        "tool_input_summary": summary,
        "cwd": event.cwd,
        "received_at": now,
    })


async def get_all_sessions() -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM sessions ORDER BY last_event_at DESC"
    )
    sessions = []
    for row in rows:
        d = dict(row)
        d["files_changed"] = json.loads(d.get("files_changed") or "[]")
        sessions.append(d)
    return sessions


async def get_session_events(session_id: str, limit: int = 50) -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM events WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    )
    return [dict(row) for row in rows]
