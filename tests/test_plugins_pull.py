"""Tests for `hydra_cli plugins pull` - install, marketplace + layer render, and
deferred prune. Runs against a fake api.get and a tmp HOME (no live server)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from hydra_cli import plugins as plugins_mod

from tests.test_config_plugins import INVALID_PATH_SETS, VALID_PATH_SETS

MANIFEST = ".claude-plugin/plugin.json"


def plugin(*rels: str, enabled: bool = True, instances: list[str] | None = None) -> dict:
    files = {MANIFEST: "{}\n", **{rel: f"{rel}\n" for rel in rels}}
    return {"files": files, "enabled": enabled, "instances": instances}


class FakePull:
    def __init__(self) -> None:
        self.served: object = {}
        self.status = 200
        self.raw: str | None = None

    def get(self, path: str) -> tuple[int, str]:
        assert path == "/api/config/plugins"
        return self.status, self.raw if self.raw is not None else json.dumps(self.served)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HYDRA_INSTANCE_ID", "pi")
    fake = FakePull()
    monkeypatch.setattr(plugins_mod.api, "get", fake.get)
    claude = tmp_path / ".claude"
    return fake, claude, claude / "hydra-plugins"


def layer(claude: Path) -> dict:
    return json.loads((claude / "settings.plugins.json").read_text())


def marketplace(root: Path) -> dict:
    return json.loads((root / ".claude-plugin" / "marketplace.json").read_text())


def state(claude: Path) -> dict:
    return json.loads((claude / ".hydra-plugins.json").read_text())


@pytest.mark.parametrize("paths", VALID_PATH_SETS)
def test_client_valid_paths_accepted(paths):
    assert plugins_mod.path_error(paths) is None


@pytest.mark.parametrize("paths", INVALID_PATH_SETS)
def test_client_invalid_paths_rejected(paths):
    assert plugins_mod.path_error(paths) is not None


def test_pull_installs_files_marketplace_and_layer(env, capsys):
    fake, claude, root = env
    fake.served = {"look": plugin("commands/look.md"), "alpha": plugin()}
    assert plugins_mod.run_pull() == 0
    assert (root / "look" / "commands" / "look.md").read_text() == "commands/look.md\n"
    assert (root / "alpha" / ".claude-plugin" / "plugin.json").read_text() == "{}\n"
    assert marketplace(root) == {
        "name": "hydra",
        "owner": {"name": "Hydra"},
        "plugins": [
            {"name": "alpha", "source": "./alpha"},
            {"name": "look", "source": "./look"},
        ],
    }
    assert layer(claude) == {
        "extraKnownMarketplaces": {
            "hydra": {"source": {"source": "directory", "path": root.as_posix()}}
        },
        "enabledPlugins": {"alpha@hydra": True, "look@hydra": True},
    }
    assert "\\" not in layer(claude)["extraKnownMarketplaces"]["hydra"]["source"]["path"]
    assert set(state(claude)["managed"]) == {
        str(root / "look" / ".claude-plugin" / "plugin.json"),
        str(root / "look" / "commands" / "look.md"),
        str(root / "alpha" / ".claude-plugin" / "plugin.json"),
    }
    assert state(claude)["dropped"] == []
    out = capsys.readouterr().out
    assert "plugins pull: 3 written, 0 unchanged, 0 pruned, 0 refused, 2 enabled" in out


def test_second_pull_reports_unchanged(env, capsys):
    fake, _claude, _root = env
    fake.served = {"look": plugin("commands/look.md")}
    plugins_mod.run_pull()
    capsys.readouterr()
    assert plugins_mod.run_pull() == 0
    assert "0 written, 2 unchanged, 0 pruned, 0 refused, 1 enabled" in capsys.readouterr().out


def test_symlink_target_refused_and_plugin_not_enabled(env, tmp_path: Path):
    fake, claude, root = env
    outside = tmp_path / "outside.md"
    outside.write_text("theirs\n")
    (root / "look" / "commands").mkdir(parents=True)
    os.symlink(outside, root / "look" / "commands" / "look.md")
    fake.served = {"look": plugin("commands/look.md"), "other": plugin()}
    assert plugins_mod.run_pull() == 1
    assert outside.read_text() == "theirs\n"
    assert layer(claude)["enabledPlugins"] == {"other@hydra": True}
    assert [p["name"] for p in marketplace(root)["plugins"]] == ["other"]
    # The written part of a partially refused plugin is still owned.
    assert str(root / "look" / ".claude-plugin" / "plugin.json") in state(claude)["managed"]
    assert str(root / "look" / "commands" / "look.md") not in state(claude)["managed"]


def test_non_regular_target_refused(env):
    fake, claude, root = env
    (root / "look" / "commands" / "look.md").mkdir(parents=True)
    fake.served = {"look": plugin("commands/look.md")}
    assert plugins_mod.run_pull() == 1
    assert layer(claude) == {}
    assert marketplace(root)["plugins"] == []


def test_unmanaged_file_refused_then_adopted(env):
    fake, claude, root = env
    target = root / "look" / "commands" / "look.md"
    target.parent.mkdir(parents=True)
    target.write_text("hand-made\n")
    fake.served = {"look": plugin("commands/look.md")}
    assert plugins_mod.run_pull() == 1
    assert target.read_text() == "hand-made\n"
    assert layer(claude) == {}

    assert plugins_mod.run_pull(adopt=True) == 0
    assert target.read_text() == "commands/look.md\n"
    assert layer(claude)["enabledPlugins"] == {"look@hydra": True}


def test_owned_file_is_rewritten_without_adopt(env):
    fake, _claude, root = env
    fake.served = {"look": plugin("commands/look.md")}
    plugins_mod.run_pull()
    fake.served = {"look": {"files": {MANIFEST: "{}\n", "commands/look.md": "v2\n"}}}
    assert plugins_mod.run_pull() == 0
    assert (root / "look" / "commands" / "look.md").read_text() == "v2\n"


def test_file_removed_from_served_plugin_is_pruned_one_pull_late(env):
    fake, claude, root = env
    fake.served = {"look": plugin("a.md", "sub/b.md")}
    plugins_mod.run_pull()
    fake.served = {"look": plugin("a.md")}
    assert plugins_mod.run_pull() == 0
    b = root / "look" / "sub" / "b.md"
    assert b.exists()
    assert state(claude)["dropped"] == [str(b)]
    assert layer(claude)["enabledPlugins"] == {"look@hydra": True}

    plugins_mod.run_pull()
    assert not b.exists()
    assert not b.parent.exists()
    assert state(claude)["dropped"] == []
    assert (root / "look" / "a.md").exists()


def test_removed_plugin_is_disabled_then_pruned(env, capsys):
    fake, claude, root = env
    fake.served = {"keep": plugin(), "gone": plugin("commands/x.md")}
    plugins_mod.run_pull()
    fake.served = {"keep": plugin()}
    plugins_mod.run_pull()
    assert (root / "gone" / "commands" / "x.md").exists()
    assert layer(claude)["enabledPlugins"] == {"keep@hydra": True}
    assert [p["name"] for p in marketplace(root)["plugins"]] == ["keep"]

    capsys.readouterr()
    plugins_mod.run_pull()
    assert not (root / "gone").exists()
    assert root.is_dir()
    assert "2 pruned" in capsys.readouterr().out


def test_empty_server_keeps_files_and_empties_layer(env, capsys):
    fake, claude, root = env
    fake.served = {"look": plugin("commands/look.md")}
    plugins_mod.run_pull()
    before = state(claude)
    fake.served = {}
    assert plugins_mod.run_pull() == 0
    assert (root / "look" / "commands" / "look.md").exists()
    assert layer(claude) == {}
    assert marketplace(root)["plugins"] == []
    assert state(claude) == before
    assert "nothing pruned" in capsys.readouterr().err

    # Still no authority on the next empty pull either.
    plugins_mod.run_pull()
    assert (root / "look" / "commands" / "look.md").exists()


@pytest.mark.parametrize(
    ("status", "raw"), [(500, "boom"), (200, "{not json"), (200, "[1, 2]")]
)
def test_fetch_failure_touches_nothing(env, status: int, raw: str):
    fake, claude, root = env
    fake.served = {"look": plugin("commands/look.md")}
    plugins_mod.run_pull()
    snapshot = {
        path: path.read_bytes()
        for path in [
            claude / "settings.plugins.json",
            claude / ".hydra-plugins.json",
            root / ".claude-plugin" / "marketplace.json",
        ]
    }
    fake.status, fake.raw = status, raw
    assert plugins_mod.run_pull() == 1
    assert {path: path.read_bytes() for path in snapshot} == snapshot
    assert (root / "look" / "commands" / "look.md").exists()


def test_disabled_and_other_instance_plugins_are_not_installed(env):
    fake, claude, root = env
    fake.served = {
        "off": plugin(enabled=False),
        "gpu-only": plugin(instances=["gpu"]),
        "pi-only": plugin(instances=["pi"]),
    }
    assert plugins_mod.run_pull() == 0
    assert not (root / "off").exists()
    assert not (root / "gpu-only").exists()
    assert layer(claude)["enabledPlugins"] == {"pi-only@hydra": True}


def test_disabling_a_plugin_drops_then_prunes_it(env):
    fake, claude, root = env
    fake.served = {"look": plugin()}
    plugins_mod.run_pull()
    fake.served = {"look": plugin(enabled=False)}
    plugins_mod.run_pull()
    assert layer(claude) == {}
    assert (root / "look" / ".claude-plugin" / "plugin.json").exists()
    plugins_mod.run_pull()
    assert not (root / "look").exists()


@pytest.mark.parametrize(
    "served",
    [
        {"bad": {"files": {"a/../../escape.md": "x"}}},
        {"bad": {"files": {"a\\b.md": "x"}}},
        {"bad": {"files": {"A.md": "x", "a.md": "y"}}},
        {"bad": {"files": {}}},
        {"bad": {"files": {"a.md": 1}}},
        {"bad": "not a dict"},
        {".bad": plugin()},
    ],
)
def test_malformed_plugin_is_skipped_client_side(env, tmp_path: Path, served, capsys):
    fake, claude, root = env
    fake.served = {**served, "good": plugin()}
    assert plugins_mod.run_pull() == 0
    assert "skip" in capsys.readouterr().err
    assert layer(claude)["enabledPlugins"] == {"good@hydra": True}
    assert not (root / "bad").exists()
    assert not (tmp_path / ".claude" / "escape.md").exists()


def test_state_entries_outside_the_root_are_never_pruned(env, tmp_path: Path):
    fake, claude, _root = env
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n")
    claude.mkdir(parents=True, exist_ok=True)
    (claude / ".hydra-plugins.json").write_text(
        json.dumps({"managed": [], "dropped": [str(victim)]})
    )
    fake.served = {"look": plugin()}
    plugins_mod.run_pull()
    assert victim.exists()
