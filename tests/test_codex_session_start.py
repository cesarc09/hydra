"""Tests for the Codex SessionStart adapter and hooks writer."""

from __future__ import annotations

import io
import json
import stat
from pathlib import Path
from typing import Any

import pytest
from hydra_cli import codex as codex_mod


@pytest.fixture
def session_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    memory = tmp_path / "memory"
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(codex_mod, "memory_dir_for_cwd", lambda cwd: memory)
    monkeypatch.setattr(codex_mod, "run_sync", lambda cwd: calls.append(("sync", cwd)) or 0)
    monkeypatch.setattr(
        codex_mod,
        "run_pull",
        lambda harness: calls.append(("pull", harness)) or 0,
    )
    return memory, calls


def hook_output(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)


def test_session_start_emits_only_json_with_small_index(
    session_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    memory, calls = session_env
    memory.mkdir()
    (memory / "MEMORY.md").write_text("# Index\n")
    monkeypatch.setattr(codex_mod.sys, "stdin", io.StringIO('{"cwd": "/project"}'))
    assert codex_mod.run_session_start() == 0
    output = hook_output(capsys)["hookSpecificOutput"]
    assert output == {
        "hookEventName": "SessionStart",
        "additionalContext": f"Hydra memory index: {memory}\n\n# Index\n",
    }
    assert calls == [("sync", "/project"), ("pull", "codex-cli")]


@pytest.mark.parametrize("index", [None, "x" * 8000])
def test_session_start_uses_header_when_index_missing_or_over_cap(
    session_env, index: str | None, monkeypatch: pytest.MonkeyPatch, capsys
):
    memory, _calls = session_env
    if index is not None:
        memory.mkdir()
        (memory / "MEMORY.md").write_text(index)
    monkeypatch.setattr(codex_mod.sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(codex_mod.os, "getcwd", lambda: "/fallback")
    assert codex_mod.run_session_start() == 0
    output = hook_output(capsys)["hookSpecificOutput"]
    assert output["additionalContext"] == f"Hydra memory index: {memory}"


def test_session_start_survives_sync_exception_and_empty_stdin(
    session_env, monkeypatch: pytest.MonkeyPatch, capsys
):
    memory, calls = session_env

    def fail(_cwd: str) -> int:
        raise RuntimeError("offline")

    monkeypatch.setattr(codex_mod, "run_sync", fail)
    monkeypatch.setattr(codex_mod.sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(codex_mod.os, "getcwd", lambda: "/fallback")
    assert codex_mod.run_session_start() == 0
    assert hook_output(capsys)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert calls == [("pull", "codex-cli")]


def test_session_start_survives_closed_stdin(
    session_env, monkeypatch: pytest.MonkeyPatch, capsys
):
    _memory, calls = session_env
    stdin = io.StringIO()
    stdin.close()
    monkeypatch.setattr(codex_mod.sys, "stdin", stdin)
    monkeypatch.setattr(codex_mod.os, "getcwd", lambda: "/fallback")

    assert codex_mod.run_session_start() == 0
    assert hook_output(capsys)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert calls == [("sync", "/fallback"), ("pull", "codex-cli")]


@pytest.fixture
def setup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "codex" / "hooks.json"
    pulls: list[str] = []
    monkeypatch.setattr(codex_mod, "hooks_path", lambda: path)
    monkeypatch.setattr(codex_mod, "run_pull", lambda harness: pulls.append(harness) or 0)
    return path, pulls


def test_codex_setup_wires_fresh_file_and_is_idempotent(setup_env):
    path, pulls = setup_env
    assert codex_mod.run_setup() == 0
    first = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(first)["hooks"]["SessionStart"] == [
        {
            "matcher": "startup|resume|clear|compact",
            "hooks": [
                {
                    "type": "command",
                    "command": "python -m hydra_cli codex-session-start",
                    "timeout": 20,
                    "statusMessage": "Syncing Hydra context",
                }
            ],
        }
    ]
    assert codex_mod.run_setup() == 0
    assert path.read_bytes() == first
    assert pulls == ["codex-cli", "codex-cli"]


def test_codex_setup_collapses_stale_entries_and_keeps_foreign(setup_env):
    path, _pulls = setup_env
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "other": True,
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "old-one",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "python -m hydra_cli skills pull --harness codex-cli"
                                    ),
                                    "timeout": 1,
                                },
                                {"type": "command", "command": "foreign command"},
                            ],
                        },
                        {
                            "matcher": "old-two",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python -m hydra_cli sync --pull",
                                    "timeout": 2,
                                }
                            ],
                        },
                    ]
                },
            }
        )
    )
    assert codex_mod.run_setup() == 0
    config = json.loads(path.read_text())
    assert config["other"] is True
    groups = config["hooks"]["SessionStart"]
    assert len(groups) == 1
    group = groups[0]
    assert group["matcher"] == "old-one"
    assert group["hooks"][0] == codex_mod._ENTRY
    assert group["hooks"][1] == {"type": "command", "command": "foreign command"}
    assert sum(
        entry.get("command") == "python -m hydra_cli codex-session-start"
        for current in groups
        for entry in current["hooks"]
    ) == 1
    assert all(current["hooks"] for current in groups)


@pytest.mark.parametrize("contents", ["[]", "not json"])
def test_codex_setup_refuses_invalid_json_object(setup_env, contents: str):
    path, pulls = setup_env
    path.parent.mkdir()
    path.write_text(contents)
    before = path.read_bytes()
    assert codex_mod.run_setup() == 1
    assert path.read_bytes() == before
    assert pulls == []


def test_codex_setup_refuses_symlink(setup_env, tmp_path: Path):
    path, pulls = setup_env
    target = tmp_path / "real-hooks.json"
    target.write_text("{}")
    path.parent.mkdir()
    path.symlink_to(target)
    assert codex_mod.run_setup() == 1
    assert target.read_text() == "{}"
    assert pulls == []
