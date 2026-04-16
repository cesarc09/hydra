"""Tests for memory CRUD endpoints."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _create_memory(client: AsyncClient, **overrides) -> dict:
    payload = {
        "name": "test memory",
        "description": "a test",
        "type": "user",
        "body": "some content",
        **overrides,
    }
    res = await client.post("/api/memory", json=payload)
    assert res.status_code == 201
    return res.json()


# --- List / Get ---


async def test_list_memories_empty(client: AsyncClient):
    res = await client.get("/api/memory")
    assert res.status_code == 200
    assert res.json() == []


async def test_create_and_get_memory(client: AsyncClient):
    created = await _create_memory(client, name="user role", type="user")
    assert created["id"] == 1
    assert created["name"] == "user role"
    assert created["type"] == "user"

    res = await client.get(f"/api/memory/{created['id']}")
    assert res.status_code == 200
    assert res.json()["name"] == "user role"


async def test_list_returns_all(client: AsyncClient):
    await _create_memory(client, name="m1")
    await _create_memory(client, name="m2")

    res = await client.get("/api/memory")
    assert len(res.json()) == 2


# --- Update ---


async def test_update_partial(client: AsyncClient):
    created = await _create_memory(client, name="old name", body="old body")
    mid = created["id"]

    res = await client.put(f"/api/memory/{mid}", json={"name": "new name"})
    assert res.status_code == 200
    updated = res.json()
    assert updated["name"] == "new name"
    assert updated["body"] == "old body"  # unchanged


async def test_update_nonexistent_returns_404(client: AsyncClient):
    res = await client.put("/api/memory/999", json={"name": "x"})
    assert res.status_code == 404


async def test_update_empty_body_returns_400(client: AsyncClient):
    created = await _create_memory(client)
    res = await client.put(f"/api/memory/{created['id']}", json={})
    assert res.status_code == 400


# --- Delete ---


async def test_delete_memory(client: AsyncClient):
    created = await _create_memory(client)
    res = await client.delete(f"/api/memory/{created['id']}")
    assert res.status_code == 204

    res = await client.get(f"/api/memory/{created['id']}")
    assert res.status_code == 404


async def test_delete_nonexistent_returns_404(client: AsyncClient):
    res = await client.delete("/api/memory/999")
    assert res.status_code == 404


# --- Type validation ---


async def test_invalid_type_rejected(client: AsyncClient):
    res = await client.post("/api/memory", json={
        "name": "bad", "type": "invalid", "body": "x",
    })
    assert res.status_code == 422


# --- Auth ---


async def test_memory_auth_required(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret")
    res = await client.get("/api/memory")
    assert res.status_code == 401
