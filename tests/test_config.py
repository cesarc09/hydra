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


async def test_put_empty_claude_md_rejected(client: AsyncClient):
    """Blanking the user-level CLAUDE.md is destructive across every machine
    that pulls from this Hydra instance; the PUT endpoint refuses empty or
    whitespace-only content."""
    for body in ("", "   ", "\n\n\t"):
        res = await client.put(
            "/api/config/claude-md",
            content=body,
            headers={"Content-Type": "text/plain"},
        )
        assert res.status_code == 400, f"expected 400 for body={body!r}"


async def test_put_empty_does_not_overwrite(client: AsyncClient):
    await client.put(
        "/api/config/claude-md",
        content="keep me",
        headers={"Content-Type": "text/plain"},
    )
    res = await client.put(
        "/api/config/claude-md",
        content="",
        headers={"Content-Type": "text/plain"},
    )
    assert res.status_code == 400
    res = await client.get("/api/config/claude-md")
    assert res.text == "keep me"


async def test_claude_md_auth_required(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret")
    res = await client.get("/api/config/claude-md")
    assert res.status_code == 401

    res = await client.get(
        "/api/config/claude-md",
        headers={"Authorization": "Bearer secret"},
    )
    assert res.status_code == 200


async def test_alias_literal_marker_without_variants_renders_verbatim(client: AsyncClient):
    content = "Leave {{x}} untouched until a harness variant exists."
    response = await client.put("/api/config/claude-md", content=content)
    assert response.status_code == 200
    rendered = await client.get("/api/config/skills/instructions/claude-code")
    assert rendered.text == content


async def test_alias_rejects_new_marker_missing_from_existing_variant(client: AsyncClient):
    await client.put(
        "/api/config/skills/instructions",
        json={
            "kind": "instructions",
            "common": "Use {{agent}}.",
            "variants": {"codex-cli": {"agent": "Codex"}},
        },
    )
    response = await client.put(
        "/api/config/claude-md", content="Use {{agent}} in {{mode}}."
    )
    assert response.status_code == 422
    assert "codex-cli" in response.json()["detail"]
    assert "mode" in response.json()["detail"]


async def test_alias_put_preserves_harness_variants(client: AsyncClient):
    await client.put(
        "/api/config/skills/instructions",
        json={
            "kind": "instructions",
            "common": "Old {{agent}}.",
            "variants": {"codex-cli": {"agent": "Codex"}},
        },
    )
    response = await client.put("/api/config/claude-md", content="New {{agent}}.")
    assert response.status_code == 200
    rendered = await client.get("/api/config/skills/instructions/codex-cli")
    assert rendered.text == "New Codex."
