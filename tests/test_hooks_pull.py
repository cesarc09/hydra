"""Tests for `hydra_cli hooks pull` - install, wire, and scoped prune.

Exercises run_pull with a fake api.get and a tmp hooks dir (no live server).
The invariant most of these guard is "wire only what is on disk": wiring for a
script that never landed would hard-deny every tool call, because python on a
missing file exits 2 and PreToolUse reads exit 2 as "block".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hydra_cli import hooks as hooks_mod

GOOD = "import sys\nsys.exit(0)\n"
BROKEN = "def oops(:\n"


def spec(**over):
    base = {
        "content": GOOD,
        "runtime": "python",
        "event": "PreToolUse",
        "matcher": None,
        "timeout": 10,
        "enabled": True,
        "instances": None,
    }
    base.update(over)
    return base


class FakePull:
    """Stand-in for the served hook map. `served` is read at call time, so tests
    can reassign it between pulls to simulate the server changing."""

    def __init__(self) -> None:
        self.served: dict[str, dict] = {}
        self.status = 200

    def get(self, path: str) -> tuple[int, str]:
        assert path == "/api/config/hooks"
        return self.status, json.dumps(self.served)


@pytest.fixture
def pull_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    hdir = tmp_path / "hooks"
    monkeypatch.setattr(hooks_mod, "hooks_dir", lambda: hdir)
    monkeypatch.delenv("HYDRA_POLICY_HOOKS_DISABLE", raising=False)
    monkeypatch.setenv("HYDRA_INSTANCE_ID", "pi")
    fake = FakePull()
    monkeypatch.setattr(hooks_mod.api, "get", fake.get)
    # State file and wiring live at hooks_dir().parent => tmp_path.
    return fake, hdir


def wiring(hdir: Path) -> dict:
    return json.loads((hdir.parent / "settings.hooks.json").read_text())["hooks"]


def state(hdir: Path) -> dict:
    return json.loads((hdir.parent / ".hydra-hooks.json").read_text())


def test_pull_writes_script_and_wiring(pull_env):
    fake, hdir = pull_env
    fake.served = {"guard": spec(matcher="Agent")}
    assert hooks_mod.run_pull() == 0
    assert (hdir / "guard.py").read_text() == GOOD
    assert wiring(hdir) == {
        "PreToolUse": [
            {
                "matcher": "Agent",
                "hooks": [
                    {
                        "type": "command",
                        "command": 'python "$HOME/.claude/hooks/guard.py"',
                        "timeout": 10,
                    }
                ],
            }
        ]
    }
    assert state(hdir) == {"managed": ["guard.py"]}


def test_pull_warns_when_the_interpreter_is_missing(
    pull_env, monkeypatch: pytest.MonkeyPatch, capsys
):
    """The Windows shape: scripts install, wiring renders, nothing runs. Wire it
    anyway - that exits 127, not the blocking 2 - but do not go silent."""
    fake, hdir = pull_env
    fake.served = {"guard": spec()}
    monkeypatch.setattr(hooks_mod.shutil, "which", lambda _name: None)
    assert hooks_mod.run_pull() == 0
    assert "'python' is not on PATH" in capsys.readouterr().err
    assert wiring(hdir)["PreToolUse"]


def test_pull_script_is_executable(pull_env):
    fake, hdir = pull_env
    fake.served = {"guard": spec()}
    hooks_mod.run_pull()
    assert (hdir / "guard.py").stat().st_mode & 0o111


def test_pull_omits_matcher_key_when_unset(pull_env):
    fake, hdir = pull_env
    fake.served = {"role": spec(event="SubagentStart")}
    hooks_mod.run_pull()
    assert "matcher" not in wiring(hdir)["SubagentStart"][0]


def test_pull_bash_runtime_uses_sh_and_bash(pull_env):
    fake, hdir = pull_env
    fake.served = {"note": spec(runtime="bash", content="echo hi\n")}
    hooks_mod.run_pull()
    assert (hdir / "note.sh").read_text() == "echo hi\n"
    entry = wiring(hdir)["PreToolUse"][0]["hooks"][0]
    assert entry["command"] == 'bash "$HOME/.claude/hooks/note.sh"'


def test_pull_disabled_hook_is_not_installed_or_wired(pull_env):
    fake, hdir = pull_env
    fake.served = {"guard": spec(enabled=False)}
    hooks_mod.run_pull()
    assert not (hdir / "guard.py").exists()
    assert wiring(hdir) == {}


def test_pull_skips_hook_scoped_to_other_instances(pull_env):
    fake, hdir = pull_env
    fake.served = {"cluster": spec(instances=["lnode01"])}
    hooks_mod.run_pull()
    assert not (hdir / "cluster.py").exists()
    assert wiring(hdir) == {}


def test_pull_installs_hook_scoped_to_this_instance(pull_env):
    fake, hdir = pull_env
    fake.served = {"cluster": spec(instances=["lnode01", "pi"])}
    hooks_mod.run_pull()
    assert (hdir / "cluster.py").exists()
    assert wiring(hdir)["PreToolUse"]


def test_pull_disabled_hook_prunes_its_stale_script(pull_env):
    fake, hdir = pull_env
    fake.served = {"guard": spec()}
    hooks_mod.run_pull()
    fake.served = {"guard": spec(enabled=False)}
    hooks_mod.run_pull()
    assert not (hdir / "guard.py").exists()
    assert wiring(hdir) == {}


def test_pull_broken_script_keeps_previous_version_and_stays_wired(pull_env):
    """A syntax error must never overwrite a working guard. The last-good script
    stays on disk and stays wired - running the previous version beats running
    nothing for a fail-open hook."""
    fake, hdir = pull_env
    fake.served = {"guard": spec()}
    hooks_mod.run_pull()
    fake.served = {"guard": spec(content=BROKEN)}
    assert hooks_mod.run_pull() == 0
    assert (hdir / "guard.py").read_text() == GOOD
    assert wiring(hdir)["PreToolUse"]
    assert state(hdir) == {"managed": ["guard.py"]}


def test_pull_broken_script_with_no_previous_emits_no_wiring(pull_env):
    """The exit-2 guard: nothing on disk means nothing wired. Wiring here would
    hard-deny every matching tool call on this machine."""
    fake, hdir = pull_env
    fake.served = {"guard": spec(content=BROKEN)}
    assert hooks_mod.run_pull() == 0
    assert not (hdir / "guard.py").exists()
    assert wiring(hdir) == {}


def test_pull_prunes_server_removed(pull_env):
    fake, hdir = pull_env
    fake.served = {"guard": spec(), "role": spec(event="SubagentStart")}
    hooks_mod.run_pull()
    fake.served = {"guard": spec()}
    hooks_mod.run_pull()
    assert (hdir / "guard.py").exists()
    assert not (hdir / "role.py").exists()
    assert "SubagentStart" not in wiring(hdir)


def test_pull_leaves_hand_authored_untouched(pull_env):
    fake, hdir = pull_env
    hdir.mkdir(parents=True)
    (hdir / "mine.py").write_text("local-only")
    fake.served = {"guard": spec()}
    hooks_mod.run_pull()
    fake.served = {"role": spec(event="SubagentStart")}
    hooks_mod.run_pull()
    assert (hdir / "mine.py").read_text() == "local-only"
    assert not (hdir / "guard.py").exists()


def test_pull_empty_server_does_not_prune(pull_env):
    """An empty server is never authority to delete: a wrong HYDRA_URL and a
    fresh DB look identical to "every hook was deleted". The wiring layer still
    empties, so the retained scripts are inert."""
    fake, hdir = pull_env
    fake.served = {"guard": spec()}
    hooks_mod.run_pull()
    fake.served = {}
    assert hooks_mod.run_pull() == 0
    assert (hdir / "guard.py").exists()
    assert wiring(hdir) == {}


def test_pull_disable_env_empties_wiring_but_keeps_scripts(pull_env, monkeypatch):
    fake, hdir = pull_env
    fake.served = {"guard": spec()}
    hooks_mod.run_pull()
    monkeypatch.setenv("HYDRA_POLICY_HOOKS_DISABLE", "1")
    assert hooks_mod.run_pull() == 0
    assert (hdir / "guard.py").exists()
    assert wiring(hdir) == {}


def test_pull_skips_unsafe_name(pull_env):
    fake, hdir = pull_env
    fake.served = {"ok": spec(), "../evil": spec()}
    assert hooks_mod.run_pull() == 0
    assert (hdir / "ok.py").exists()
    assert not (hdir.parent / "evil.py").exists()
    assert state(hdir) == {"managed": ["ok.py"]}


def test_pull_skips_unknown_runtime(pull_env):
    fake, hdir = pull_env
    fake.served = {"weird": spec(runtime="perl")}
    assert hooks_mod.run_pull() == 0
    assert wiring(hdir) == {}


def test_pull_fetch_error_is_nonfatal_and_leaves_layer_alone(pull_env):
    fake, hdir = pull_env
    fake.served = {"guard": spec()}
    hooks_mod.run_pull()
    before = (hdir.parent / "settings.hooks.json").read_text()
    fake.status = 500
    assert hooks_mod.run_pull() == 1
    assert (hdir / "guard.py").exists()
    assert (hdir.parent / "settings.hooks.json").read_text() == before


def test_pull_multiple_hooks_same_event_are_ordered_by_server(pull_env):
    fake, hdir = pull_env
    fake.served = {"a-guard": spec(), "b-guard": spec()}
    hooks_mod.run_pull()
    commands = [g["hooks"][0]["command"] for g in wiring(hdir)["PreToolUse"]]
    assert commands == [
        'python "$HOME/.claude/hooks/a-guard.py"',
        'python "$HOME/.claude/hooks/b-guard.py"',
    ]
