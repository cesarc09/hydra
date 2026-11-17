"""Tests for the server-distributed plugin endpoints and `hydra plugins put|list`."""

import json
import os
from pathlib import Path

import hydra_cli.__main__ as cli_mod
import pytest
from httpx import AsyncClient

import server.app as app_mod
import server.db as db_module
from server.routers.config import plugin_path_error

# Shared with tests/test_plugins_pull.py, which runs them against the client copy.
VALID_PATH_SETS = [
    ["plugin.json"],
    [".claude-plugin/plugin.json", "commands/look.md", "scripts/run.py"],
    ["a..b/c d.txt"],
    ["README.md", "docs/README.md"],
]
INVALID_PATH_SETS = [
    [""],
    ["/etc/passwd"],
    ["a\\b"],
    ["a\\..\\..\\x"],
    ["C:x"],
    ["file.txt:stream"],
    ["a/../b"],
    [".."],
    ["a/./b"],
    ["."],
    ["a//b"],
    ["a/"],
    ["name."],
    ["dir /file"],
    ["a\x00b"],
    ["a\nb"],
    ["\x1fx"],
    ["README.md", "readme.md"],
    ["Dir/a.md", "dir/A.md"],
]


def body(**over):
    base: dict = {"files": {".claude-plugin/plugin.json": "{}\n", "commands/look.md": "Look\n"}}
    base.update(over)
    return base


@pytest.mark.parametrize("paths", VALID_PATH_SETS)
def test_valid_paths_accepted(paths):
    assert plugin_path_error(paths) is None


@pytest.mark.parametrize("paths", INVALID_PATH_SETS)
def test_invalid_paths_rejected(paths):
    error = plugin_path_error(paths)
    assert error is not None


async def test_plugin_map_empty(client: AsyncClient):
    res = await client.get("/api/config/plugins")
    assert res.status_code == 200
    assert res.json() == {}


async def test_put_then_get_plugin(client: AsyncClient):
    res = await client.put("/api/config/plugins/look", json=body(instances=["pi"]))
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    res = await client.get("/api/config/plugins")
    assert res.json() == {
        "look": {
            "files": {".claude-plugin/plugin.json": "{}\n", "commands/look.md": "Look\n"},
            "enabled": True,
            "instances": ["pi"],
        }
    }


async def test_put_replaces_whole_plugin(client: AsyncClient):
    await client.put("/api/config/plugins/look", json=body(instances=["pi"]))
    await client.put(
        "/api/config/plugins/look", json={"files": {"only.md": "x"}, "enabled": False}
    )
    res = await client.get("/api/config/plugins")
    assert res.json()["look"] == {"files": {"only.md": "x"}, "enabled": False, "instances": None}


async def test_plugin_map_is_ordered_by_name(client: AsyncClient):
    for name in ["zeta", "alpha", "mid"]:
        await client.put(f"/api/config/plugins/{name}", json=body())
    res = await client.get("/api/config/plugins")
    assert list(res.json()) == ["alpha", "mid", "zeta"]


async def test_invalid_stored_instances_degrade_to_none(client: AsyncClient):
    await client.put("/api/config/plugins/look", json=body())
    conn = db_module._db
    assert conn is not None
    await conn.execute("UPDATE config_plugins SET instances = '{not json'")
    await conn.commit()
    res = await client.get("/api/config/plugins")
    assert res.json()["look"]["instances"] is None


@pytest.mark.parametrize("name", [".hidden", "-x", "a.b", "a b"])
async def test_put_invalid_name_rejected(client: AsyncClient, name: str):
    res = await client.put(f"/api/config/plugins/{name}", json=body())
    assert res.status_code == 400
    assert (await client.get("/api/config/plugins")).json() == {}


async def test_put_empty_files_rejected(client: AsyncClient):
    res = await client.put("/api/config/plugins/look", json={"files": {}})
    assert res.status_code == 400


@pytest.mark.parametrize("paths", INVALID_PATH_SETS)
async def test_put_invalid_paths_rejected(client: AsyncClient, paths: list[str]):
    res = await client.put(
        "/api/config/plugins/look", json={"files": dict.fromkeys(paths, "x")}
    )
    assert res.status_code == 400
    assert res.json()["detail"]
    assert (await client.get("/api/config/plugins")).json() == {}


async def test_put_error_names_the_bad_path(client: AsyncClient):
    res = await client.put(
        "/api/config/plugins/look", json={"files": {"ok.md": "x", "a/../b": "x"}}
    )
    assert res.status_code == 400
    assert "a/../b" in res.json()["detail"]


async def test_delete_plugin(client: AsyncClient):
    await client.put("/api/config/plugins/look", json=body())
    assert (await client.delete("/api/config/plugins/look")).status_code == 204
    assert (await client.get("/api/config/plugins")).json() == {}


async def test_delete_missing_plugin_404(client: AsyncClient):
    assert (await client.delete("/api/config/plugins/missing")).status_code == 404


async def test_plugins_auth_required(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("server.config.AUTH_TOKEN", "secret")
    assert (await client.get("/api/config/plugins")).status_code == 401
    assert (await client.put("/api/config/plugins/look", json=body())).status_code == 401
    res = await client.get("/api/config/plugins", headers={"Authorization": "Bearer secret"})
    assert res.status_code == 200


# --- body limit: the prefix rule ---


async def test_plugin_body_above_default_cap_accepted(client: AsyncClient):
    """300 KiB exceeds the 256 KiB default; the plugins prefix raises it to 1 MiB."""
    res = await client.put(
        "/api/config/plugins/big", json={"files": {"blob.txt": "x" * (300 * 1024)}}
    )
    assert res.status_code == 200


async def test_plugin_body_over_prefix_cap_rejected(client: AsyncClient):
    res = await client.put(
        "/api/config/plugins/big", json={"files": {"blob.txt": "x" * (1024 * 1024)}}
    )
    assert res.status_code == 413


async def test_exact_path_cap_beats_prefix(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(app_mod._BODY_LIMITS, "/api/config/plugins/tiny", 1024)
    payload = {"files": {"blob.txt": "x" * 2048}}
    assert (await client.put("/api/config/plugins/tiny", json=payload)).status_code == 413
    assert (await client.put("/api/config/plugins/other", json=payload)).status_code == 200


# --- CLI: plugins put / list ---


def _args(*args: str):
    return cli_mod.build_parser().parse_args(["plugins", *args])


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list:
    recorded: list = []
    monkeypatch.setattr(
        cli_mod.api,
        "put_json",
        lambda path, payload: recorded.append((path, payload)) or (200, '{"status":"ok"}'),
    )
    return recorded


def _plugin_dir(tmp_path: Path) -> Path:
    root = tmp_path / "look"
    (root / ".claude-plugin" / "types").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "look"}\n')
    (root / ".claude-plugin" / "types" / "gen.d.ts").write_text("generated\n")
    (root / "commands").mkdir()
    (root / "commands" / "look.md").write_text("Look\n")
    return root


async def test_cli_put_payload_shape(
    tmp_path: Path, calls: list, capsys: pytest.CaptureFixture[str]
):
    root = _plugin_dir(tmp_path)
    cli_mod.cmd_plugins_put(
        _args("put", "look", str(root), "--disabled", "--instances", "pi, gpu")
    )
    payload = {
        "files": {".claude-plugin/plugin.json": '{"name": "look"}\n', "commands/look.md": "Look\n"},
        "enabled": False,
        "instances": ["pi", "gpu"],
    }
    assert calls == [("/api/config/plugins/look", payload)]
    size = len(json.dumps(payload).encode())
    assert f"payload {size} bytes" in capsys.readouterr().err


async def test_cli_put_skips_only_top_level_generated_types(tmp_path: Path, calls: list):
    root = _plugin_dir(tmp_path)
    (root / "src" / "types").mkdir(parents=True)
    (root / "src" / "types" / "kept.ts").write_text("kept\n")
    cli_mod.cmd_plugins_put(_args("put", "look", str(root)))
    files = calls[0][1]["files"]
    assert "src/types/kept.ts" in files
    assert not any(key.startswith(".claude-plugin/types/") for key in files)
    assert calls[0][1]["enabled"] is True
    assert "instances" not in calls[0][1]


@pytest.mark.parametrize("kind", ["dir", "file"])
async def test_cli_put_skips_git_metadata(tmp_path: Path, calls: list, kind: str):
    root = _plugin_dir(tmp_path)
    if kind == "dir":
        (root / ".git").mkdir()
        (root / ".git" / "index").write_bytes(b"\xff\xfe binary")
    else:
        (root / ".git").write_text("gitdir: /elsewhere\n")
    cli_mod.cmd_plugins_put(_args("put", "look", str(root)))
    assert not any(key.split("/")[0] == ".git" for key in calls[0][1]["files"])


@pytest.mark.parametrize("kind", ["file", "dir"])
async def test_cli_put_refuses_symlinks(tmp_path: Path, calls: list, kind: str):
    root = _plugin_dir(tmp_path)
    target = tmp_path / "outside"
    if kind == "file":
        target.write_text("secret\n")
    else:
        target.mkdir()
        (target / "x.md").write_text("x\n")
    os.symlink(target, root / "commands" / "link")
    with pytest.raises(SystemExit) as exc:
        cli_mod.cmd_plugins_put(_args("put", "look", str(root)))
    assert exc.value.code == 1
    assert calls == []


async def test_cli_put_refuses_non_utf8(
    tmp_path: Path, calls: list, capsys: pytest.CaptureFixture[str]
):
    root = _plugin_dir(tmp_path)
    (root / "icon.png").write_bytes(b"\x89PNG\xff\xfe")
    with pytest.raises(SystemExit) as exc:
        cli_mod.cmd_plugins_put(_args("put", "look", str(root)))
    assert exc.value.code == 1
    assert calls == []
    assert "not UTF-8" in capsys.readouterr().err


async def test_cli_put_refuses_unsafe_path(tmp_path: Path, calls: list):
    root = _plugin_dir(tmp_path)
    (root / "trailing.").write_text("x\n")
    with pytest.raises(SystemExit):
        cli_mod.cmd_plugins_put(_args("put", "look", str(root)))
    assert calls == []


async def test_cli_list_prints_counts_and_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    served = {
        "alpha": {"files": {"a": "1", "b": "2"}, "enabled": True, "instances": None},
        "beta": {"files": {"a": "1"}, "enabled": False, "instances": ["pi", "gpu"]},
    }
    monkeypatch.setattr(cli_mod.api, "get", lambda path: (200, json.dumps(served)))
    cli_mod.cmd_plugins_list(_args("list"))
    assert capsys.readouterr().out.splitlines() == [
        "alpha\t2 files",
        "beta\t1 files  [disabled, instances=pi,gpu]",
    ]
