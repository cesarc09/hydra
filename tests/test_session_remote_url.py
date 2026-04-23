"""Tests for the per-session Remote Control URL endpoint."""

import asyncio

import pytest
from httpx import AsyncClient

from server.services.session_manager import subscribe, unsubscribe

pytestmark = pytest.mark.asyncio

HEADERS = {"X-Instance-Id": "test-machine"}
VALID_URL = "https://claude.ai/code/session_018hbZgbrb3GtAbeHnLZNcRm"


def hook_body(session_id: str, event_name: str, **kwargs) -> dict:
    return {"session_id": session_id, "hook_event_name": event_name, **kwargs}


async def _start(client: AsyncClient, sid: str) -> None:
    res = await client.post(
        "/api/hooks/event",
        json=hook_body(sid, "SessionStart", cwd="/tmp"),
        headers=HEADERS,
    )
    assert res.status_code == 200


async def test_put_valid_url_stores_and_returns(client: AsyncClient):
    await _start(client, "s1")

    res = await client.put(
        "/api/sessions/s1/remote-control-url", json={"url": VALID_URL}
    )
    assert res.status_code == 200
    assert res.json() == {"remote_control_url": VALID_URL}

    sessions = (await client.get("/api/sessions")).json()
    assert sessions[0]["remote_control_url"] == VALID_URL


async def test_put_empty_string_clears(client: AsyncClient):
    await _start(client, "s1")
    await client.put("/api/sessions/s1/remote-control-url", json={"url": VALID_URL})

    res = await client.put("/api/sessions/s1/remote-control-url", json={"url": ""})
    assert res.status_code == 200
    assert res.json() == {"remote_control_url": None}

    sessions = (await client.get("/api/sessions")).json()
    assert sessions[0]["remote_control_url"] is None


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://evil.example/session_abc",
        "http://claude.ai/code/session_abc",  # http, not https
        "https://claude.ai/code/other_abc",  # wrong prefix
        "javascript:alert(1)",
        "https://claude.ai/code/session_abc extra",  # trailing content
        "not a url at all",
    ],
)
async def test_put_malformed_url_returns_400(client: AsyncClient, bad_url: str):
    await _start(client, "s1")
    res = await client.put(
        "/api/sessions/s1/remote-control-url", json={"url": bad_url}
    )
    assert res.status_code == 400


async def test_put_oversized_url_returns_422(client: AsyncClient):
    await _start(client, "s1")
    # Pydantic max_length=256 → 422 (validation error), not 400
    huge = "https://claude.ai/code/session_" + "a" * 300
    res = await client.put(
        "/api/sessions/s1/remote-control-url", json={"url": huge}
    )
    assert res.status_code == 422


async def test_put_missing_session_returns_404(client: AsyncClient):
    res = await client.put(
        "/api/sessions/nope/remote-control-url", json={"url": VALID_URL}
    )
    assert res.status_code == 404


async def test_session_end_clears_url(client: AsyncClient):
    await _start(client, "s1")
    await client.put("/api/sessions/s1/remote-control-url", json={"url": VALID_URL})

    res = await client.post(
        "/api/hooks/event",
        json=hook_body("s1", "SessionEnd", source="user_exit"),
        headers=HEADERS,
    )
    assert res.status_code == 200

    sessions = (await client.get("/api/sessions")).json()
    assert sessions[0]["status"] == "ended"
    assert sessions[0]["remote_control_url"] is None


async def test_put_broadcasts_session_url_updated(client: AsyncClient):
    await _start(client, "s1")
    queue = subscribe()
    try:
        # Drain any in-flight events from the SessionStart above.
        while not queue.empty():
            queue.get_nowait()

        res = await client.put(
            "/api/sessions/s1/remote-control-url", json={"url": VALID_URL}
        )
        assert res.status_code == 200

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["event_name"] == "session_url_updated"
        assert event["session_id"] == "s1"
        assert event["remote_control_url"] == VALID_URL
    finally:
        unsubscribe(queue)
