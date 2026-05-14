"""Tests for the per-path request body size cap."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

HEADERS = {"X-Instance-Id": "test-machine"}

# /api/hooks/event is capped at 64 KB.
_OVER_CAP = 70 * 1024


async def test_small_body_passes(client: AsyncClient):
    body = {"session_id": "s1", "hook_event_name": "SessionStart", "cwd": "/tmp/p"}
    res = await client.post("/api/hooks/event", json=body, headers=HEADERS)
    assert res.status_code == 200


async def test_oversized_content_length_rejected(client: AsyncClient):
    """Honest Content-Length over the cap is rejected before the body is read."""
    body = {
        "session_id": "s1",
        "hook_event_name": "SessionStart",
        "blob": "x" * _OVER_CAP,
    }
    res = await client.post("/api/hooks/event", json=body, headers=HEADERS)
    assert res.status_code == 413


async def test_oversized_chunked_body_rejected(client: AsyncClient):
    """A chunked request carries no Content-Length, so the cap must also be
    enforced on the bytes as they stream in."""

    async def chunks():
        sent = 0
        while sent < _OVER_CAP:
            yield b"x" * 8192
            sent += 8192

    res = await client.post(
        "/api/hooks/event",
        content=chunks(),
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert res.status_code == 413
