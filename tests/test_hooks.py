"""Tests for hook ingestion endpoint and session state machine."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

HEADERS = {"X-Instance-Id": "test-machine"}


def hook_body(session_id: str, event_name: str, **kwargs) -> dict:
    """Build a minimal hook event payload."""
    return {"session_id": session_id, "hook_event_name": event_name, **kwargs}


async def post_event(client: AsyncClient, body: dict) -> None:
    res = await client.post("/api/hooks/event", json=body, headers=HEADERS)
    assert res.status_code == 200


# --- Hook endpoint ---


async def test_hook_returns_200(client: AsyncClient):
    body = hook_body("s1", "SessionStart", cwd="/tmp/project")
    res = await client.post("/api/hooks/event", json=body, headers=HEADERS)
    assert res.status_code == 200
    assert res.json() == {}


async def test_hook_rejects_bad_auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret-token")
    body = hook_body("s1", "SessionStart")
    res = await client.post(
        "/api/hooks/event",
        json=body,
        headers={**HEADERS, "Authorization": "Bearer wrong"},
    )
    assert res.status_code == 401


async def test_hook_accepts_valid_auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret-token")
    body = hook_body("s1", "SessionStart", cwd="/tmp")
    res = await client.post(
        "/api/hooks/event",
        json=body,
        headers={**HEADERS, "Authorization": "Bearer secret-token"},
    )
    assert res.status_code == 200


# --- State machine transitions ---


async def test_session_start_creates_active_session(client: AsyncClient):
    await post_event(client, hook_body("s1", "SessionStart", cwd="/projects/hydra"))

    res = await client.get("/api/sessions")
    sessions = res.json()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s1"
    assert sessions[0]["status"] == "active"
    assert sessions[0]["instance_id"] == "test-machine"
    assert sessions[0]["cwd"] == "/projects/hydra"


async def test_stop_sets_idle(client: AsyncClient):
    await post_event(client, hook_body("s1", "SessionStart", cwd="/tmp"))
    await post_event(client, hook_body("s1", "Stop"))

    res = await client.get("/api/sessions")
    assert res.json()[0]["status"] == "idle"


async def test_notification_idle_prompt_sets_waiting_input(client: AsyncClient):
    await post_event(client, hook_body("s1", "SessionStart", cwd="/tmp"))
    await post_event(
        client,
        hook_body("s1", "Notification", notification_type="idle_prompt"),
    )

    res = await client.get("/api/sessions")
    assert res.json()[0]["status"] == "waiting_input"


async def test_user_prompt_reactivates_session(client: AsyncClient):
    await post_event(client, hook_body("s1", "SessionStart", cwd="/tmp"))
    await post_event(client, hook_body("s1", "Stop"))
    await post_event(client, hook_body("s1", "UserPromptSubmit"))

    res = await client.get("/api/sessions")
    assert res.json()[0]["status"] == "active"


async def test_session_end(client: AsyncClient):
    await post_event(client, hook_body("s1", "SessionStart", cwd="/tmp"))
    await post_event(client, hook_body("s1", "SessionEnd", source="user_exit"))

    res = await client.get("/api/sessions")
    assert res.json()[0]["status"] == "ended"
    assert res.json()[0]["end_reason"] == "user_exit"


# --- PostToolUse tracking ---


async def test_post_tool_use_tracks_last_tool(client: AsyncClient):
    await post_event(client, hook_body("s1", "SessionStart", cwd="/tmp"))
    await post_event(
        client,
        hook_body("s1", "PostToolUse", tool_name="Bash", tool_input={"command": "ls -la"}),
    )

    res = await client.get("/api/sessions")
    session = res.json()[0]
    assert session["last_tool"] == "Bash"
    assert session["last_tool_input_summary"] == "ls -la"


async def test_write_tool_tracks_files_changed(client: AsyncClient):
    await post_event(client, hook_body("s1", "SessionStart", cwd="/tmp"))
    await post_event(
        client,
        hook_body(
            "s1", "PostToolUse",
            tool_name="Write",
            tool_input={"file_path": "/tmp/foo.py"},
        ),
    )
    await post_event(
        client,
        hook_body(
            "s1", "PostToolUse",
            tool_name="Edit",
            tool_input={"file_path": "/tmp/bar.py"},
        ),
    )
    # Duplicate file should not appear twice
    await post_event(
        client,
        hook_body(
            "s1", "PostToolUse",
            tool_name="Edit",
            tool_input={"file_path": "/tmp/foo.py"},
        ),
    )

    res = await client.get("/api/sessions")
    files = res.json()[0]["files_changed"]
    assert files == ["/tmp/foo.py", "/tmp/bar.py"]


# --- Event recording ---


async def test_events_are_recorded(client: AsyncClient):
    await post_event(client, hook_body("s1", "SessionStart", cwd="/tmp"))
    await post_event(client, hook_body("s1", "Stop"))

    res = await client.get("/api/sessions/s1/events")
    events = res.json()
    assert len(events) == 2
    event_names = [e["event_name"] for e in events]
    # Events are returned most-recent-first
    assert event_names == ["Stop", "SessionStart"]


# --- Multiple sessions ---


async def test_multiple_sessions_tracked_independently(client: AsyncClient):
    await post_event(client, hook_body("s1", "SessionStart", cwd="/project-a"))
    await post_event(client, hook_body("s2", "SessionStart", cwd="/project-b"))
    await post_event(client, hook_body("s1", "Stop"))

    res = await client.get("/api/sessions")
    sessions = {s["session_id"]: s for s in res.json()}
    assert sessions["s1"]["status"] == "idle"
    assert sessions["s2"]["status"] == "active"
