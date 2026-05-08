"""Tests for project registry endpoints."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _create_project(
    client: AsyncClient, *, instance_id: str = "host-1", **overrides
) -> dict:
    payload = {
        "slug": "hydra",
        "path": "/home/user/projects/hydra",
        "description": "Claude Code control plane",
        **overrides,
    }
    res = await client.post(
        "/api/projects", json=payload, headers={"X-Instance-Id": instance_id}
    )
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
    assert created["paths"] == [{
        "instance_id": "host-1",
        "path": "/home/user/projects/hydra",
        "auto_registered_at": None,
    }]
    assert "path" not in created  # top-level path removed
    assert created["auto_registered_at"] is None

    res = await client.get("/api/projects/hydra")
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "hydra"
    assert body["paths"][0]["instance_id"] == "host-1"


async def test_list_returns_all(client: AsyncClient):
    await _create_project(client, slug="hydra", path="/p/hydra")
    await _create_project(client, slug="webapp", path="/p/webapp")

    res = await client.get("/api/projects")
    assert len(res.json()) == 2


# --- Multi-path registration ---


async def test_same_slug_different_machine_adds_path(client: AsyncClient):
    """Registering the same slug from a second machine appends a path row
    rather than returning 409."""
    await _create_project(
        client, instance_id="vps", path="/home/giosue/projects/hydra"
    )
    res = await client.post(
        "/api/projects",
        json={"slug": "hydra", "path": "C:\\Users\\giosu\\projects\\hydra"},
        headers={"X-Instance-Id": "laptop"},
    )
    assert res.status_code == 201
    body = res.json()
    paths = {p["instance_id"]: p["path"] for p in body["paths"]}
    assert paths == {
        "vps": "/home/giosue/projects/hydra",
        "laptop": "C:\\Users\\giosu\\projects\\hydra",
    }


async def test_same_slug_same_machine_updates_path(client: AsyncClient):
    """Same (slug, instance_id) with a new path updates in place."""
    await _create_project(client, instance_id="host-1", path="/old/path")
    res = await client.post(
        "/api/projects",
        json={"slug": "hydra", "path": "/new/path"},
        headers={"X-Instance-Id": "host-1"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["paths"] == [{
        "instance_id": "host-1",
        "path": "/new/path",
        "auto_registered_at": None,
    }]


async def test_delete_single_path(client: AsyncClient):
    await _create_project(client, instance_id="vps", path="/srv/hydra")
    await _create_project(client, instance_id="laptop", path="/Users/x/hydra")

    res = await client.delete("/api/projects/hydra/paths/laptop")
    assert res.status_code == 204

    body = (await client.get("/api/projects/hydra")).json()
    assert [p["instance_id"] for p in body["paths"]] == ["vps"]


async def test_delete_path_not_found(client: AsyncClient):
    await _create_project(client)
    res = await client.delete("/api/projects/hydra/paths/nope")
    assert res.status_code == 404


# --- Update ---


async def test_update_description(client: AsyncClient):
    await _create_project(client)
    res = await client.put("/api/projects/hydra", json={"description": "updated"})
    assert res.status_code == 200
    assert res.json()["description"] == "updated"


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


async def test_delete_cascades_paths(client: AsyncClient):
    """Deleting the project also removes its path rows (FK ON DELETE CASCADE)."""
    await _create_project(client, instance_id="a", path="/a")
    await _create_project(client, instance_id="b", path="/b")
    res = await client.delete("/api/projects/hydra")
    assert res.status_code == 204

    # Re-create and verify no stale paths linger
    recreated = await _create_project(client, instance_id="c", path="/c")
    assert recreated["paths"] == [{
        "instance_id": "c", "path": "/c", "auto_registered_at": None,
    }]


async def test_delete_nonexistent_returns_404(client: AsyncClient):
    res = await client.delete("/api/projects/nope")
    assert res.status_code == 404


# --- Auth ---


async def test_projects_auth_required(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret")
    res = await client.get("/api/projects")
    assert res.status_code == 401


# --- Auto-register ---


async def _auto_register(client: AsyncClient, cwd: str, instance_id: str = "host-1"):
    return await client.post(
        "/api/projects/auto-register",
        json={"cwd": cwd},
        headers={"X-Instance-Id": instance_id},
    )


async def test_auto_register_creates_new_slug(client: AsyncClient):
    res = await _auto_register(client, "/home/giosue/projects/scratchpad")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "created"
    assert body["slug"] == "scratchpad"

    proj = (await client.get("/api/projects/scratchpad")).json()
    assert proj["auto_registered_at"] is not None
    assert proj["paths"][0]["instance_id"] == "host-1"
    assert proj["paths"][0]["auto_registered_at"] is not None


async def test_auto_register_attaches_to_existing_slug(client: AsyncClient):
    """Slug 'hydra' exists from machine A; machine B auto-registers a cwd
    whose basename matches → only the path row is flagged, the project
    record's auto flag stays clear (since it was manually created)."""
    await _create_project(
        client, instance_id="vps", path="/home/giosue/projects/hydra"
    )
    res = await _auto_register(
        client, r"C:\Users\giosu\projects\hydra", instance_id="laptop"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "attached"
    assert body["slug"] == "hydra"

    proj = (await client.get("/api/projects/hydra")).json()
    assert proj["auto_registered_at"] is None  # manually created, untouched
    laptop_path = next(p for p in proj["paths"] if p["instance_id"] == "laptop")
    assert laptop_path["auto_registered_at"] is not None
    vps_path = next(p for p in proj["paths"] if p["instance_id"] == "vps")
    assert vps_path["auto_registered_at"] is None


async def test_auto_register_idempotent_on_existing_path(client: AsyncClient):
    """Same machine + same cwd called twice returns 'existing' on the
    second call without writing again."""
    first = await _auto_register(client, "/home/giosue/projects/foo")
    assert first.json()["status"] == "created"
    second = await _auto_register(client, "/home/giosue/projects/foo")
    assert second.status_code == 200
    assert second.json() == {
        "status": "existing", "slug": "foo", "reason": None,
    }


async def test_auto_register_skips_stoplist(client: AsyncClient):
    res = await _auto_register(client, "/home/giosue/Downloads")
    assert res.status_code == 200
    assert res.json()["status"] == "skipped"
    assert "stoplist" in res.json()["reason"]
    # Nothing was written
    assert (await client.get("/api/projects")).json() == []


async def test_auto_register_skips_root_paths(client: AsyncClient):
    res = await _auto_register(client, "/")
    assert res.json()["status"] == "skipped"
    assert (await client.get("/api/projects")).json() == []


async def test_auto_register_normalizes_slug(client: AsyncClient):
    """Spaces and mixed case in basename → normalized slug."""
    res = await _auto_register(client, "/home/giosue/My Stuff")
    assert res.json() == {"status": "created", "slug": "my-stuff", "reason": None}


# --- Confirm ---


async def test_confirm_project_clears_flag(client: AsyncClient):
    await _auto_register(client, "/home/giosue/projects/scratch")
    res = await client.post("/api/projects/scratch/confirm")
    assert res.status_code == 204
    proj = (await client.get("/api/projects/scratch")).json()
    assert proj["auto_registered_at"] is None
    # Path-level flag is independent and untouched
    assert proj["paths"][0]["auto_registered_at"] is not None


async def test_confirm_path_clears_flag(client: AsyncClient):
    await _create_project(client, instance_id="vps", path="/srv/hydra")
    await _auto_register(
        client, r"C:\Users\giosu\projects\hydra", instance_id="laptop"
    )
    res = await client.post("/api/projects/hydra/paths/laptop/confirm")
    assert res.status_code == 204
    proj = (await client.get("/api/projects/hydra")).json()
    laptop = next(p for p in proj["paths"] if p["instance_id"] == "laptop")
    assert laptop["auto_registered_at"] is None


async def test_confirm_nonexistent_returns_404(client: AsyncClient):
    res = await client.post("/api/projects/nope/confirm")
    assert res.status_code == 404
    res = await client.post("/api/projects/nope/paths/host/confirm")
    assert res.status_code == 404


async def test_manual_create_has_no_auto_flag(client: AsyncClient):
    """The pre-existing manual-create endpoint stays unflagged."""
    await _create_project(client)
    proj = (await client.get("/api/projects/hydra")).json()
    assert proj["auto_registered_at"] is None
    assert proj["paths"][0]["auto_registered_at"] is None
