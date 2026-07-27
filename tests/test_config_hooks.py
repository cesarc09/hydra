"""Tests for the server-distributed policy-hook endpoints.

(Distinct from test_hooks.py, which covers hook *event ingestion*.)
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


def body(**over):
    base = {"content": "import sys\n", "event": "PreToolUse"}
    base.update(over)
    return base


async def test_hook_map_empty(client: AsyncClient):
    res = await client.get("/api/config/hooks")
    assert res.status_code == 200
    assert res.json() == {}


async def test_put_then_get_hook(client: AsyncClient):
    res = await client.put(
        "/api/config/hooks/guard",
        json=body(content="print(1)\n", matcher="Agent", timeout=15),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    res = await client.get("/api/config/hooks")
    assert res.json() == {
        "guard": {
            "content": "print(1)\n",
            "runtime": "python",
            "event": "PreToolUse",
            "matcher": "Agent",
            "timeout": 15,
            "enabled": True,
            "instances": None,
        }
    }

    # Single-name fetch returns the script body byte-for-byte.
    res = await client.get("/api/config/hooks/guard")
    assert res.status_code == 200
    assert res.text == "print(1)\n"
    assert res.headers["content-type"] == "text/plain; charset=utf-8"


async def test_put_hook_replaces_all_metadata(client: AsyncClient):
    await client.put("/api/config/hooks/guard", json=body(matcher="Agent", timeout=15))
    await client.put(
        "/api/config/hooks/guard",
        json=body(content="v2\n", event="SubagentStart", runtime="bash"),
    )
    spec = (await client.get("/api/config/hooks")).json()["guard"]
    assert spec["content"] == "v2\n"
    assert spec["event"] == "SubagentStart"
    assert spec["runtime"] == "bash"
    # An upsert that omits an optional field resets it rather than merging.
    assert spec["matcher"] is None
    assert spec["timeout"] == 10


async def test_instances_round_trip_as_list(client: AsyncClient):
    await client.put("/api/config/hooks/cluster", json=body(instances=["lnode01"]))
    spec = (await client.get("/api/config/hooks")).json()["cluster"]
    assert spec["instances"] == ["lnode01"]


async def test_hook_map_is_ordered_by_name(client: AsyncClient):
    """Two hooks on one event must render in a stable order, or every pull
    rewrites settings.json."""
    for name in ("zeta", "alpha", "mid"):
        await client.put(f"/api/config/hooks/{name}", json=body())
    assert list((await client.get("/api/config/hooks")).json()) == ["alpha", "mid", "zeta"]


async def test_disabled_hook_is_still_served(client: AsyncClient):
    """`enabled` is the fleet-wide off switch; the client decides what to do
    with it, so the row keeps being served."""
    await client.put("/api/config/hooks/guard", json=body(enabled=False))
    assert (await client.get("/api/config/hooks")).json()["guard"]["enabled"] is False


async def test_put_empty_content_rejected(client: AsyncClient):
    for content in ("", "   ", "\n\n\t"):
        res = await client.put("/api/config/hooks/guard", json=body(content=content))
        assert res.status_code in (400, 422), f"expected reject for {content!r}"


async def test_put_empty_does_not_overwrite(client: AsyncClient):
    await client.put("/api/config/hooks/guard", json=body(content="keep me\n"))
    res = await client.put("/api/config/hooks/guard", json=body(content="   "))
    assert res.status_code == 400
    assert (await client.get("/api/config/hooks/guard")).text == "keep me\n"


@pytest.mark.parametrize("name", [".hidden", "foo.bar", "foo bar", "-lead"])
async def test_put_invalid_name_rejected(client: AsyncClient, name: str):
    res = await client.put(f"/api/config/hooks/{name}", json=body())
    assert res.status_code == 400


@pytest.mark.parametrize(
    "over",
    [
        {"runtime": "perl"},
        {"event": ""},
        {"event": "Pre ToolUse"},
        {"timeout": 0},
        {"timeout": 601},
        {"instances": "lnode01"},
    ],
)
async def test_put_invalid_metadata_rejected(client: AsyncClient, over: dict):
    res = await client.put("/api/config/hooks/guard", json=body(**over))
    assert res.status_code == 422


async def test_get_missing_hook_404(client: AsyncClient):
    assert (await client.get("/api/config/hooks/nope")).status_code == 404


async def test_delete_hook(client: AsyncClient):
    await client.put("/api/config/hooks/guard", json=body())
    assert (await client.delete("/api/config/hooks/guard")).status_code == 204
    assert (await client.get("/api/config/hooks/guard")).status_code == 404


async def test_delete_missing_hook_404(client: AsyncClient):
    assert (await client.delete("/api/config/hooks/nope")).status_code == 404


async def test_hooks_auth_required(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret")
    assert (await client.get("/api/config/hooks")).status_code == 401
    res = await client.get("/api/config/hooks", headers={"Authorization": "Bearer secret"})
    assert res.status_code == 200
