"""Tests for CLAUDE.md config endpoints."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_get_claude_md_empty(client: AsyncClient):
    res = await client.get("/api/config/claude-md")
    assert res.status_code == 200
    assert res.text == ""
    assert res.headers["content-type"] == "text/plain; charset=utf-8"


async def test_put_then_get_claude_md(client: AsyncClient):
    content = "# Personal Rules\n\n- Be concise\n- Prefer simple solutions\n"
    res = await client.put(
        "/api/config/claude-md",
        content=content,
        headers={"Content-Type": "text/plain"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "updated_at" in data

    res = await client.get("/api/config/claude-md")
    assert res.text == content


async def test_put_claude_md_replaces(client: AsyncClient):
    await client.put(
        "/api/config/claude-md",
        content="version 1",
        headers={"Content-Type": "text/plain"},
    )
    await client.put(
        "/api/config/claude-md",
        content="version 2",
        headers={"Content-Type": "text/plain"},
    )
    res = await client.get("/api/config/claude-md")
    assert res.text == "version 2"


async def test_claude_md_auth_required(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret")
    res = await client.get("/api/config/claude-md")
    assert res.status_code == 401

    res = await client.get(
        "/api/config/claude-md",
        headers={"Authorization": "Bearer secret"},
    )
    assert res.status_code == 200
