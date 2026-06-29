"""Tests for the unauthenticated /api/health probe."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_health_ok(client: AsyncClient):
    res = await client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "db": "ok"}


async def test_health_needs_no_auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    """Liveness must be probeable without a token, so `hydra doctor` can tell a
    down server apart from a bad/missing auth token."""
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret")
    res = await client.get("/api/health")
    assert res.status_code == 200
