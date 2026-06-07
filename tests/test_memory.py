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
    assert res.status_code == 200
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


# --- Upsert (same-name) ---


async def test_upsert_replaces_existing_global(client: AsyncClient):
    first = await _create_memory(client, name="shared", body="v1")
    second = await _create_memory(client, name="shared", body="v2", description="new")
    assert first["id"] == second["id"]
    assert second["body"] == "v2"
    assert second["description"] == "new"

    res = await client.get("/api/memory")
    assert len(res.json()) == 1


async def test_upsert_distinct_when_project_differs(client: AsyncClient):
    await _register_project(client, "alpha", "/tmp/alpha")
    await _register_project(client, "beta", "/tmp/beta")

    a = await _create_memory(client, name="shared", project_slug="alpha", body="A")
    b = await _create_memory(client, name="shared", project_slug="beta", body="B")
    assert a["id"] != b["id"]
    assert a["project_slug"] == "alpha"
    assert b["project_slug"] == "beta"


async def test_distribute_global_to_multiple_projects(client: AsyncClient):
    """Simulates the dashboard's Move-to-projects flow: POST same name into N
    project slugs, then DELETE the global. The N project copies should remain
    intact with distinct ids."""
    await _register_project(client, "alpha", "/tmp/alpha")
    await _register_project(client, "beta", "/tmp/beta")
    await _register_project(client, "gamma", "/tmp/gamma")

    g = await _create_memory(client, name="shared", body="GLOBAL", type="user")
    assert g["project_slug"] is None
    global_id = g["id"]

    saved = []
    for slug in ("alpha", "beta", "gamma"):
        row = await _create_memory(
            client, name="shared", body="GLOBAL", type="user", project_slug=slug,
        )
        saved.append(row)

    ids = {s["id"] for s in saved}
    assert len(ids) == 3
    assert global_id not in ids

    res = await client.delete(f"/api/memory/{global_id}")
    assert res.status_code == 204

    res = await client.get("/api/memory")
    rows = res.json()
    assert len(rows) == 3
    assert {row["project_slug"] for row in rows} == {"alpha", "beta", "gamma"}
    assert all(row["name"] == "shared" for row in rows)
    assert all(row["body"] == "GLOBAL" for row in rows)

    res = await client.get(f"/api/memory/{global_id}")
    assert res.status_code == 404


async def test_distribute_overwrites_existing_project_memory(client: AsyncClient):
    """The dashboard relies on upsert semantics when a collision is confirmed:
    POSTing the same (name, project_slug) replaces the row in place."""
    await _register_project(client, "alpha", "/tmp/alpha")
    pre = await _create_memory(client, name="shared", project_slug="alpha", body="OLD")
    over = await _create_memory(client, name="shared", project_slug="alpha", body="NEW")
    assert pre["id"] == over["id"]
    assert over["body"] == "NEW"


# --- Type <-> scope coercion ---


async def test_pinned_global_type_coerced_to_project(client: AsyncClient):
    """A global type pinned to a project is auto-scoped to 'project'. Covers the
    dashboard Move/Copy-to-project flow and guards against a pinned-but-global
    row that `hydra sync` would re-globalize into a duplicate."""
    await _register_project(client, "alpha", "/tmp/alpha")
    row = await _create_memory(
        client, name="shared", type="feedback", project_slug="alpha",
    )
    assert row["project_slug"] == "alpha"
    assert row["type"] == "project"


async def test_pinned_reference_type_preserved(client: AsyncClient):
    """reference is already a project-scoped type and must pass through."""
    await _register_project(client, "alpha", "/tmp/alpha")
    row = await _create_memory(
        client, name="ref", type="reference", project_slug="alpha",
    )
    assert row["type"] == "reference"


async def test_global_type_preserved_when_unpinned(client: AsyncClient):
    """No coercion for global memories - user vs feedback is the caller's call."""
    row = await _create_memory(client, name="g", type="feedback")
    assert row["project_slug"] is None
    assert row["type"] == "feedback"


async def test_update_pinning_coerces_type(client: AsyncClient):
    """Pinning a global memory via PUT (scope changes, type not sent) coerces
    the type to 'project'."""
    await _register_project(client, "alpha", "/tmp/alpha")
    created = await _create_memory(client, name="m", type="feedback")
    assert created["type"] == "feedback"

    res = await client.put(
        f"/api/memory/{created['id']}", json={"project_slug": "alpha"},
    )
    assert res.status_code == 200
    updated = res.json()
    assert updated["project_slug"] == "alpha"
    assert updated["type"] == "project"


# --- Filtered list ---


async def test_list_filtered_by_project(client: AsyncClient):
    await _register_project(client, "alpha", "/tmp/alpha")
    await _create_memory(client, name="g1", type="user")  # global
    await _create_memory(client, name="p1", type="project", project_slug="alpha")

    res = await client.get("/api/memory?project_slug=alpha")
    names = [m["name"] for m in res.json()]
    assert names == ["p1"]


async def test_list_filtered_by_project_with_globals(client: AsyncClient):
    await _register_project(client, "alpha", "/tmp/alpha")
    await _register_project(client, "beta", "/tmp/beta")
    await _create_memory(client, name="g1", type="user")
    await _create_memory(client, name="p_alpha", type="project", project_slug="alpha")
    await _create_memory(client, name="p_beta", type="project", project_slug="beta")

    res = await client.get("/api/memory?project_slug=alpha&include_global=true")
    names = sorted(m["name"] for m in res.json())
    assert names == ["g1", "p_alpha"]


# --- Auth ---


async def test_memory_auth_required(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret")
    res = await client.get("/api/memory")
    assert res.status_code == 401


# --- Helpers ---


async def _register_project(client: AsyncClient, slug: str, path: str) -> None:
    res = await client.post("/api/projects", json={"slug": slug, "path": path})
    assert res.status_code == 201
