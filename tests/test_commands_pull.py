"""Tests for `hydra_cli commands pull` - fetch, write, and scoped prune.

Exercises run_pull with a fake api.get and a tmp commands dir (no live server).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hydra_cli import commands as commands_mod


class FakePull:
    """Stand-in for the served command map. `served` is read at call time, so
    tests can reassign it between pulls to simulate the server changing."""

    def __init__(self) -> None:
        self.served: dict[str, str] = {}
        self.status = 200

    def get(self, path: str) -> tuple[int, str]:
        assert path == "/api/config/commands"
        return self.status, json.dumps(self.served)


@pytest.fixture
def pull_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cdir = tmp_path / "commands"
    monkeypatch.setattr(commands_mod, "commands_dir", lambda: cdir)
    fake = FakePull()
    monkeypatch.setattr(commands_mod.api, "get", fake.get)
    # State file lives at commands_dir().parent / .hydra-commands.json => tmp_path.
    return fake, cdir


def test_pull_writes_files(pull_env):
    fake, cdir = pull_env
    fake.served = {"sync": "S-body", "finish": "F-body"}
    assert commands_mod.run_pull() == 0
    assert (cdir / "sync.md").read_text() == "S-body"
    assert (cdir / "finish.md").read_text() == "F-body"
    state = json.loads((cdir.parent / ".hydra-commands.json").read_text())
    assert state == {"managed": ["finish", "sync"]}


def test_pull_preserves_hyphenated_name(pull_env):
    """Names are written verbatim - a slug step would rename code-review."""
    fake, cdir = pull_env
    fake.served = {"code-review": "X"}
    assert commands_mod.run_pull() == 0
    assert (cdir / "code-review.md").read_text() == "X"
    assert not (cdir / "code_review.md").exists()


def test_pull_prunes_server_removed(pull_env):
    fake, cdir = pull_env
    fake.served = {"sync": "S", "finish": "F"}
    commands_mod.run_pull()
    # Server drops `finish`; next pull removes its file but keeps `sync`.
    fake.served = {"sync": "S"}
    commands_mod.run_pull()
    assert (cdir / "sync.md").exists()
    assert not (cdir / "finish.md").exists()


def test_pull_leaves_hand_authored_untouched(pull_env):
    fake, cdir = pull_env
    cdir.mkdir(parents=True)
    (cdir / "mine.md").write_text("local-only")
    fake.served = {"sync": "S"}
    commands_mod.run_pull()
    # Even after the managed set empties, a never-managed file survives prune.
    fake.served = {}
    commands_mod.run_pull()
    assert (cdir / "mine.md").read_text() == "local-only"
    assert not (cdir / "sync.md").exists()


def test_pull_skips_unsafe_name(pull_env):
    fake, cdir = pull_env
    fake.served = {"ok": "A", "../evil": "B"}
    assert commands_mod.run_pull() == 0
    assert (cdir / "ok.md").read_text() == "A"
    # No traversal: nothing written outside the commands dir.
    assert not (cdir.parent / "evil.md").exists()
    state = json.loads((cdir.parent / ".hydra-commands.json").read_text())
    assert state == {"managed": ["ok"]}


def test_pull_fetch_error_is_nonfatal(pull_env):
    fake, cdir = pull_env
    fake.status = 500
    fake.served = {"sync": "S"}
    assert commands_mod.run_pull() == 1
    assert not (cdir / "sync.md").exists()
