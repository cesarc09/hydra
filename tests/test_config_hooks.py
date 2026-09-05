"""Tests for the server-distributed policy-hook endpoints.

(Distinct from test_hooks.py, which covers hook *event ingestion*.)
"""

import json
from pathlib import Path

import hydra_cli.__main__ as cli_mod
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


def body(**over):
    base = {"content": "import sys\n", "event": "PreToolUse"}
    base.update(over)
    return base


def wired_body(wiring: dict, **over):
    base = {"content": "import sys\n", "wiring": wiring}
    base.update(over)
    return base


async def test_hook_map_empty(client: AsyncClient):
    res = await client.get("/api/config/hooks")
    assert res.status_code == 200
    assert res.json() == {}


async def test_render_unknown_harness_rejected(client: AsyncClient):
    res = await client.get("/api/config/hooks/render/unknown-harness")
    assert res.status_code == 422


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
            "wiring": {
                "claude-code": {
                    "event": "PreToolUse",
                    "matcher": "Agent",
                    "timeout": 15,
                }
            },
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


async def test_per_harness_wiring_renders_separately(client: AsyncClient):
    wiring = {
        "claude-code": {"event": "PreToolUse", "matcher": "Bash", "timeout": 10},
        "codex-cli": {"event": "PreToolUse", "matcher": "Bash|apply_patch", "timeout": 12},
    }
    res = await client.put("/api/config/hooks/guard", json=wired_body(wiring))
    assert res.status_code == 200

    claude = (await client.get("/api/config/hooks/render/claude-code")).json()["guard"]
    codex = (await client.get("/api/config/hooks/render/codex-cli")).json()["guard"]
    assert claude["matcher"] == "Bash"
    assert codex["matcher"] == "Bash|apply_patch"
    assert codex["timeout"] == 12


async def test_codex_only_row_is_hidden_from_legacy_get(client: AsyncClient):
    res = await client.put(
        "/api/config/hooks/codex-only",
        json=wired_body(
            {"codex-cli": {"event": "SubagentStart", "timeout": 20}}
        ),
    )
    assert res.status_code == 200
    assert (await client.get("/api/config/hooks")).json() == {}
    rendered = (await client.get("/api/config/hooks/render/codex-cli")).json()
    assert rendered["codex-only"]["event"] == "SubagentStart"


async def test_legacy_and_wiring_together_are_rejected(client: AsyncClient):
    res = await client.put(
        "/api/config/hooks/guard",
        json=body(wiring={"codex-cli": {"event": "PreToolUse"}}),
    )
    assert res.status_code == 422


async def test_put_without_legacy_metadata_or_wiring_rejected(client: AsyncClient):
    res = await client.put("/api/config/hooks/guard", json={"content": "import sys\n"})
    assert res.status_code == 422


@pytest.mark.parametrize(
    "wiring",
    [
        {"other": {"event": "PreToolUse"}},
        {"codex-cli": {"event": "Pre ToolUse"}},
        {"codex-cli": {"event": "PreToolUse", "timeout": 0}},
        {"codex-cli": {"event": "PreToolUse", "matcher": "x" * 257}},
        {},
    ],
)
async def test_invalid_wiring_rejected(client: AsyncClient, wiring: dict):
    res = await client.put(
        "/api/config/hooks/guard", json=wired_body(wiring)
    )
    assert res.status_code == 422


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


def _hook_args(*args: str):
    return cli_mod.build_parser().parse_args(["hooks", *args])


async def test_hooks_put_legacy_file_form(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    script = tmp_path / "guard.py"
    script.write_text("print('ok')\n")
    calls = []
    monkeypatch.setattr(
        cli_mod.api,
        "put_json",
        lambda path, payload: calls.append((path, payload)) or (200, '{"status":"ok"}'),
    )
    cli_mod.cmd_hooks_put(
        _hook_args(
            "put", "guard", str(script), "--event", "PreToolUse", "--matcher", "Bash"
        )
    )
    assert calls == [
        (
            "/api/config/hooks/guard",
            {
                "content": "print('ok')\n",
                "runtime": "python",
                "event": "PreToolUse",
                "timeout": 10,
                "enabled": True,
                "matcher": "Bash",
            },
        )
    ]


async def test_hooks_put_file_without_event_errors_before_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    script = tmp_path / "guard.py"
    script.write_text("pass\n")
    calls = []
    monkeypatch.setattr(
        cli_mod.api,
        "put_json",
        lambda path, payload: calls.append((path, payload)) or (200, "{}"),
    )
    with pytest.raises(SystemExit) as exc:
        cli_mod.cmd_hooks_put(_hook_args("put", "guard", str(script)))
    assert exc.value.code == 1
    assert calls == []
    assert "--event is required" in capsys.readouterr().err


async def test_hooks_put_directory_form_skips_distribute_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    directory = tmp_path / "guard"
    directory.mkdir()
    (directory / "hook.sh").write_text("exit 0\n")
    (directory / "claude-code.json").write_text(
        json.dumps({"event": "PreToolUse", "matcher": "Bash"})
    )
    (directory / "codex-cli.json").write_text(
        json.dumps({"event": "PreToolUse", "distribute": False})
    )
    calls = []
    monkeypatch.setattr(
        cli_mod.api,
        "put_json",
        lambda path, payload: calls.append((path, payload)) or (200, '{"status":"ok"}'),
    )
    cli_mod.cmd_hooks_put(
        _hook_args("put", "guard", str(directory), "--disabled", "--instances", "pi,gpu")
    )
    assert calls[0][1] == {
        "content": "exit 0\n",
        "runtime": "bash",
        "wiring": {
            "claude-code": {
                "event": "PreToolUse",
                "matcher": "Bash",
                "timeout": 10,
            }
        },
        "enabled": False,
        "instances": ["pi", "gpu"],
    }


@pytest.mark.parametrize(
    ("scripts", "configs", "extra"),
    [
        ([], {"claude-code.json": {"event": "Stop"}}, []),
        (["hook.py", "hook.sh"], {"claude-code.json": {"event": "Stop"}}, []),
        (["hook.py"], {"unknown.json": {"event": "Stop"}}, []),
        (["hook.py"], {"claude-code.json": {"event": "Pre ToolUse"}}, []),
        (["hook.py"], {"claude-code.json": {"event": "Stop", "distribute": False}}, []),
        (["hook.py"], {"claude-code.json": {"event": "Stop"}}, ["--event", "Stop"]),
    ],
)
async def test_hooks_put_directory_errors_before_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scripts: list[str],
    configs: dict[str, dict],
    extra: list[str],
):
    directory = tmp_path / "hook"
    directory.mkdir()
    for script in scripts:
        (directory / script).write_text("pass\n")
    for name, data in configs.items():
        (directory / name).write_text(json.dumps(data))
    calls = []
    monkeypatch.setattr(
        cli_mod.api,
        "put_json",
        lambda path, payload: calls.append((path, payload)) or (200, "{}"),
    )
    with pytest.raises(SystemExit):
        cli_mod.cmd_hooks_put(_hook_args("put", "guard", str(directory), *extra))
    assert calls == []


async def test_hooks_list_merges_render_endpoints(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    payloads = {
        "/api/config/hooks/render/claude-code": {
            "both": {"event": "PreToolUse", "matcher": "Bash"}
        },
        "/api/config/hooks/render/codex-cli": {
            "both": {"event": "PreToolUse", "matcher": "Bash|apply_patch"},
            "codex-only": {"event": "SubagentStart", "matcher": None},
        },
    }
    monkeypatch.setattr(
        cli_mod.api,
        "get",
        lambda path: (200, json.dumps(payloads[path])),
    )
    cli_mod.cmd_hooks_list(_hook_args("list"))
    assert capsys.readouterr().out.splitlines() == [
        "both\tclaude-code\tPreToolUse  [matcher=Bash]",
        "both\tcodex-cli\tPreToolUse  [matcher=Bash|apply_patch]",
        "codex-only\tcodex-cli\tSubagentStart",
    ]


@pytest.mark.parametrize("failed_harness", cli_mod.HARNESSES)
async def test_hooks_list_keeps_rows_when_one_harness_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed_harness: str,
):
    other_harness = next(harness for harness in cli_mod.HARNESSES if harness != failed_harness)
    responses = {
        f"/api/config/hooks/render/{failed_harness}": (404, "not found"),
        f"/api/config/hooks/render/{other_harness}": (
            200,
            json.dumps({"guard": {"event": "PreToolUse", "matcher": "Bash"}}),
        ),
    }
    monkeypatch.setattr(cli_mod.api, "get", lambda path: responses[path])

    cli_mod.cmd_hooks_list(_hook_args("list"))

    captured = capsys.readouterr()
    assert captured.out == f"guard\t{other_harness}\tPreToolUse  [matcher=Bash]\n"
    assert captured.err == f"hooks list [{failed_harness}] failed (404): not found\n"


@pytest.mark.parametrize("codex_status", [200, 404])
async def test_hooks_list_empty_results_fail_only_when_every_harness_failed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    codex_status: int,
):
    responses = {
        "/api/config/hooks/render/claude-code": (404, "not found"),
        "/api/config/hooks/render/codex-cli": (codex_status, "{}"),
    }
    monkeypatch.setattr(cli_mod.api, "get", lambda path: responses[path])

    if codex_status == 200:
        cli_mod.cmd_hooks_list(_hook_args("list"))
    else:
        with pytest.raises(SystemExit) as exc:
            cli_mod.cmd_hooks_list(_hook_args("list"))
        assert exc.value.code == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    expected = "hooks list [claude-code] failed (404): not found\n"
    if codex_status != 200:
        expected += "hooks list [codex-cli] failed (404): {}\n"
    assert captured.err == expected
