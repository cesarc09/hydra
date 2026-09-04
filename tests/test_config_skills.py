from pathlib import Path

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parent.parent


def skill_body(**overrides) -> dict:
    body = {
        "kind": "skill",
        "common": "Run with {{command}} in {{mode}}.",
        "variants": {
            "claude-code": {"command": "claude", "mode": "carefully"},
            "codex-cli": {"command": "codex", "mode": "quickly"},
        },
    }
    body.update(overrides)
    return body


async def test_publish_and_render_round_trip(client: AsyncClient):
    response = await client.put("/api/config/skills/review", json=skill_body())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    all_skills = (await client.get("/api/config/skills/claude-code")).json()
    assert all_skills == {
        "review": {
            "kind": "skill",
            "enabled": True,
            "implicit_invocation": False,
            "instances": None,
            "files": {"SKILL.md": "Run with claude in carefully."},
        }
    }
    one = await client.get("/api/config/skills/review/codex-cli")
    assert one.status_code == 200
    assert one.json() == {"SKILL.md": "Run with codex in quickly."}


async def test_shipped_debug_hydra_round_trip(client: AsyncClient):
    common = (ROOT / "client" / "skills" / "debug-hydra" / "common.md").read_text()
    response = await client.put(
        "/api/config/skills/debug-hydra",
        json={
            "kind": "skill",
            "enabled": True,
            "implicit_invocation": False,
            "instances": None,
            "common": common,
            "variants": {},
        },
    )
    assert response.status_code == 200
    rendered = await client.get("/api/config/skills/debug-hydra/claude-code")
    assert rendered.json()["SKILL.md"] == common


async def test_common_only_renders_verbatim_for_every_harness(client: AsyncClient):
    common = "Configure {{later}} when this harness gets a variant."
    await client.put(
        "/api/config/skills/common-only",
        json={"kind": "skill", "common": common},
    )
    for harness in ("claude-code", "codex-cli"):
        response = await client.get(f"/api/config/skills/common-only/{harness}")
        assert response.json() == {"SKILL.md": common}


async def test_missing_marker_rejected(client: AsyncClient):
    response = await client.put(
        "/api/config/skills/review",
        json=skill_body(variants={"codex-cli": {"command": "codex"}}),
    )
    assert response.status_code == 422
    assert "codex-cli" in response.json()["detail"]
    assert "mode" in response.json()["detail"]


@pytest.mark.parametrize(
    "name, kind",
    [("other", "instructions"), ("instructions", "skill")],
)
async def test_instructions_name_is_reserved(client: AsyncClient, name: str, kind: str):
    response = await client.put(
        f"/api/config/skills/{name}",
        json={"kind": kind, "common": "body"},
    )
    assert response.status_code == 422
    assert "reserved" in response.json()["detail"]


async def test_empty_common_rejected(client: AsyncClient):
    response = await client.put(
        "/api/config/skills/review",
        json={"kind": "skill", "common": " \n\t"},
    )
    assert response.status_code == 422
    assert "empty" in response.json()["detail"]


async def test_delete_and_missing(client: AsyncClient):
    await client.put("/api/config/skills/review", json=skill_body())
    assert (await client.delete("/api/config/skills/review")).status_code == 204
    assert (await client.delete("/api/config/skills/review")).status_code == 404


async def test_instructions_cannot_be_deleted(client: AsyncClient):
    await client.put(
        "/api/config/skills/instructions",
        json={"kind": "instructions", "common": "rules"},
    )
    response = await client.delete("/api/config/skills/instructions")
    assert response.status_code == 422


async def test_disabled_and_instances_round_trip(client: AsyncClient):
    await client.put(
        "/api/config/skills/review",
        json=skill_body(
            enabled=False,
            implicit_invocation=True,
            instances=["workstation", "pi"],
        ),
    )
    item = (await client.get("/api/config/skills/codex-cli")).json()["review"]
    assert item["enabled"] is False
    assert item["implicit_invocation"] is True
    assert item["instances"] == ["workstation", "pi"]


async def test_render_one_instructions_is_plain_text(client: AsyncClient):
    await client.put(
        "/api/config/skills/instructions",
        json={
            "kind": "instructions",
            "common": "Use {{agent}}.",
            "variants": {"claude-code": {"agent": "Claude"}},
        },
    )
    response = await client.get("/api/config/skills/instructions/claude-code")
    assert response.status_code == 200
    assert response.text == "Use Claude."
    assert response.headers["content-type"] == "text/plain; charset=utf-8"


async def test_render_missing_skill_404(client: AsyncClient):
    assert (await client.get("/api/config/skills/nope/claude-code")).status_code == 404


async def test_skills_auth_required(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret")
    assert (await client.get("/api/config/skills/claude-code")).status_code == 401
    response = await client.get(
        "/api/config/skills/claude-code",
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200


@pytest.mark.parametrize("name", [".hidden", "bad.name", "has space", "-lead"])
async def test_invalid_skill_name_rejected(client: AsyncClient, name: str):
    response = await client.put(
        f"/api/config/skills/{name}",
        json={"kind": "skill", "common": "body"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("harness", [".hidden", "bad.name", "has space", "-lead", "common"])
async def test_invalid_harness_rejected(client: AsyncClient, harness: str):
    response = await client.put(
        "/api/config/skills/review",
        json={"kind": "skill", "common": "body", "variants": {harness: {}}},
    )
    assert response.status_code == 422
