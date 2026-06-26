"""Tests for the server-distributed slash-command endpoints."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

_TEXT = {"Content-Type": "text/plain"}


async def test_command_map_empty(client: AsyncClient):
    res = await client.get("/api/config/commands")
    assert res.status_code == 200
    assert res.json() == {}


async def test_put_then_get_command(client: AsyncClient):
    content = "---\ndescription: wrap up\n---\n\n# /sync\n\nDo the thing.\n"
    res = await client.put("/api/config/commands/sync", content=content, headers=_TEXT)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "updated_at" in data

    # Single-name fetch returns the markdown byte-for-byte.
    res = await client.get("/api/config/commands/sync")
    assert res.status_code == 200
    assert res.text == content
    assert res.headers["content-type"] == "text/plain; charset=utf-8"


async def test_command_map_lists_all(client: AsyncClient):
    await client.put("/api/config/commands/sync", content="S", headers=_TEXT)
    await client.put("/api/config/commands/finish", content="F", headers=_TEXT)
    res = await client.get("/api/config/commands")
    assert res.status_code == 200
    assert res.json() == {"sync": "S", "finish": "F"}


async def test_put_command_replaces(client: AsyncClient):
    await client.put("/api/config/commands/sync", content="version 1", headers=_TEXT)
    await client.put("/api/config/commands/sync", content="version 2", headers=_TEXT)
    res = await client.get("/api/config/commands/sync")
    assert res.text == "version 2"


async def test_put_empty_command_rejected(client: AsyncClient):
    for body in ("", "   ", "\n\n\t"):
        res = await client.put("/api/config/commands/sync", content=body, headers=_TEXT)
        assert res.status_code == 400, f"expected 400 for body={body!r}"


async def test_put_empty_does_not_overwrite(client: AsyncClient):
    await client.put("/api/config/commands/sync", content="keep me", headers=_TEXT)
    res = await client.put("/api/config/commands/sync", content="", headers=_TEXT)
    assert res.status_code == 400
    res = await client.get("/api/config/commands/sync")
    assert res.text == "keep me"


@pytest.mark.parametrize("name", [".hidden", "foo.bar", "foo bar", "-lead"])
async def test_put_invalid_name_rejected(client: AsyncClient, name: str):
    """A command name becomes a filename on the client, so unsafe names are
    refused at write time. (Names with a path separator never route here.)"""
    res = await client.put(f"/api/config/commands/{name}", content="x", headers=_TEXT)
    assert res.status_code == 400


async def test_get_missing_command_404(client: AsyncClient):
    res = await client.get("/api/config/commands/nope")
    assert res.status_code == 404


async def test_delete_command(client: AsyncClient):
    await client.put("/api/config/commands/sync", content="S", headers=_TEXT)
    res = await client.delete("/api/config/commands/sync")
    assert res.status_code == 204
    res = await client.get("/api/config/commands/sync")
    assert res.status_code == 404


async def test_delete_missing_command_404(client: AsyncClient):
    res = await client.delete("/api/config/commands/nope")
    assert res.status_code == 404


async def test_commands_auth_required(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret")
    res = await client.get("/api/config/commands")
    assert res.status_code == 401

    res = await client.get(
        "/api/config/commands", headers={"Authorization": "Bearer secret"}
    )
    assert res.status_code == 200
