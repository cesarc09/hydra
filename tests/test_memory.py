"""Tests for memory/config sync endpoints."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_memory_status_not_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    import server.services.memory_sync as sync_module

    monkeypatch.setattr(sync_module, "CONFIG_REPO_PATH", "")
    res = await client.get("/api/memory/status")
    data = res.json()
    assert data["status"] == "not_configured"


async def test_sync_returns_not_configured_when_no_repo(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    import server.config as config_module
    import server.services.memory_sync as sync_module

    monkeypatch.setattr(config_module, "CONFIG_REPO_PATH", "")
    monkeypatch.setattr(sync_module, "CONFIG_REPO_PATH", "")
    res = await client.post("/api/memory/sync")
    data = res.json()
    assert data["status"] == "not_configured"
