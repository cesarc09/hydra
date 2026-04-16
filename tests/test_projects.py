"""Tests for project registry endpoints."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _create_project(client: AsyncClient, **overrides) -> dict:
    payload = {
        "slug": "hydra",
        "path": "/home/user/projects/hydra",
        "description": "Claude Code control plane",
        **overrides,
    }
    res = await client.post("/api/projects", json=payload)
    assert res.status_code == 201
    return res.json()


# --- List / Get ---


async def test_list_projects_empty(client: AsyncClient):
    res = await client.get("/api/projects")
    assert res.status_code == 200
    assert res.json() == []


async def test_create_and_get_project(client: AsyncClient):
    created = await _create_project(client)
    assert created["slug"] == "hydra"
    assert created["path"] == "/home/user/projects/hydra"

    res = await client.get("/api/projects/hydra")
    assert res.status_code == 200
    assert res.json()["slug"] == "hydra"


async def test_list_returns_all(client: AsyncClient):
    await _create_project(client, slug="hydra", path="/p/hydra")
    await _create_project(client, slug="webapp", path="/p/webapp")

    res = await client.get("/api/projects")
    assert len(res.json()) == 2


# --- Duplicate slug ---


async def test_duplicate_slug_returns_409(client: AsyncClient):
    await _create_project(client, slug="hydra")
    res = await client.post("/api/projects", json={
        "slug": "hydra", "path": "/other/path",
    })
    assert res.status_code == 409


# --- Update ---


async def test_update_partial(client: AsyncClient):
    await _create_project(client)
    res = await client.put("/api/projects/hydra", json={"description": "updated"})
    assert res.status_code == 200
    assert res.json()["description"] == "updated"
    assert res.json()["path"] == "/home/user/projects/hydra"  # unchanged


async def test_update_nonexistent_returns_404(client: AsyncClient):
    res = await client.put("/api/projects/nope", json={"description": "x"})
    assert res.status_code == 404


async def test_update_empty_body_returns_400(client: AsyncClient):
    await _create_project(client)
    res = await client.put("/api/projects/hydra", json={})
    assert res.status_code == 400


# --- Delete ---


async def test_delete_project(client: AsyncClient):
    await _create_project(client)
    res = await client.delete("/api/projects/hydra")
    assert res.status_code == 204

    res = await client.get("/api/projects/hydra")
    assert res.status_code == 404


async def test_delete_nonexistent_returns_404(client: AsyncClient):
    res = await client.delete("/api/projects/nope")
    assert res.status_code == 404


# --- Auth ---


async def test_projects_auth_required(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret")
    res = await client.get("/api/projects")
    assert res.status_code == 401
