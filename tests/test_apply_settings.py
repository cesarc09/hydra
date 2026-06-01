"""Tests for `hydra apply-settings` - merge Hydra template + user prefs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hydra_cli.apply_settings import (
    cmd_apply_settings,
    merge,
    migrate_user_settings,
    substitute,
)


def test_substitute_replaces_both_placeholders() -> None:
    raw = '{"u": "__HYDRA_URL__", "r": "__HYDRA_REPO_PATH__"}'
    out = substitute(raw, "https://h.example", "/repo")
    assert out == '{"u": "https://h.example", "r": "/repo"}'


def test_merge_hooks_concatenate_hydra_first() -> None:
    hydra = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "http", "url": "h"}]}],
        }
    }
    user = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "u"}]}],
        }
    }
    out = merge(hydra, user)
    assert len(out["hooks"]["SessionStart"]) == 2
    assert out["hooks"]["SessionStart"][0]["hooks"][0]["url"] == "h"
    assert out["hooks"]["SessionStart"][1]["hooks"][0]["command"] == "u"


def test_merge_user_only_event_added() -> None:
    hydra = {"hooks": {"SessionStart": [{"hooks": []}]}}
    user = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "x"}]}]}}
    out = merge(hydra, user)
    assert out["hooks"]["SessionStart"] == [{"hooks": []}]
    assert out["hooks"]["PreToolUse"] == user["hooks"]["PreToolUse"]


def test_merge_hydra_only_event_preserved() -> None:
    hydra = {"hooks": {"SessionStart": [{"hooks": [{"type": "http"}]}]}}
    user = {"hooks": {}}
    out = merge(hydra, user)
    assert out["hooks"]["SessionStart"] == hydra["hooks"]["SessionStart"]


def test_merge_non_hooks_user_wins() -> None:
    hydra = {"effortLevel": "high", "autoUpdatesChannel": "latest"}
    user = {"effortLevel": "max", "attribution": {"pr": "", "commit": ""}}
    out = merge(hydra, user)
    assert out["effortLevel"] == "max"
    assert out["attribution"] == {"pr": "", "commit": ""}
    assert out["autoUpdatesChannel"] == "latest"


def test_migrate_drops_stale_max_effort_level() -> None:
    out, changed = migrate_user_settings({"effortLevel": "max", "other": 1})
    assert changed
    assert "effortLevel" not in out
    assert out["other"] == 1


def test_migrate_keeps_explicit_effort_level() -> None:
    out, changed = migrate_user_settings({"effortLevel": "low"})
    assert not changed
    assert out["effortLevel"] == "low"


def test_migrate_moves_default_mode_into_permissions() -> None:
    out, changed = migrate_user_settings({"defaultMode": "plan"})
    assert changed
    assert "defaultMode" not in out
    assert out["permissions"] == {"defaultMode": "plan"}


def test_migrate_default_mode_keeps_existing_permissions_entry() -> None:
    out, changed = migrate_user_settings(
        {"defaultMode": "auto", "permissions": {"defaultMode": "plan", "allow": ["Bash"]}}
    )
    assert changed
    assert "defaultMode" not in out
    # An explicit permissions.defaultMode wins over the legacy top-level key.
    assert out["permissions"] == {"defaultMode": "plan", "allow": ["Bash"]}


def test_migrate_noop_on_current_format() -> None:
    current = {"effortLevel": "xhigh", "permissions": {"defaultMode": "auto"}}
    out, changed = migrate_user_settings(current)
    assert not changed
    assert out == current


def _ns(**kwargs: str) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def test_apply_scaffolds_user_file_as_template_copy(tmp_path: Path) -> None:
    hydra_tpl = tmp_path / "hydra.json"
    hydra_tpl.write_text(json.dumps({"hooks": {}, "effortLevel": "high"}))
    user_tpl = tmp_path / "user.template.json"
    user_tpl.write_text(json.dumps({"effortLevel": "xhigh"}))
    user_file = tmp_path / "user.json"
    output = tmp_path / "out.json"

    cmd_apply_settings(
        _ns(
            hydra_template=str(hydra_tpl),
            user_template=str(user_tpl),
            user_file=str(user_file),
            output=str(output),
            hydra_url="http://h",
            hydra_repo_path="/r",
        )
    )

    # Scaffold copies the template so users see all knobs and edit in place.
    assert json.loads(user_file.read_text()) == {"effortLevel": "xhigh"}
    assert json.loads(output.read_text())["effortLevel"] == "xhigh"


def test_apply_template_default_flows_when_user_file_lacks_key(tmp_path: Path) -> None:
    """Regression: a user file without `statusLine` must NOT mask the template default."""
    hydra_tpl = tmp_path / "hydra.json"
    hydra_tpl.write_text(json.dumps({"hooks": {}}))
    user_tpl = tmp_path / "user.template.json"
    user_tpl.write_text(
        json.dumps(
            {"statusLine": {"type": "command", "command": "~/.claude/statusline.sh"}}
        )
    )
    user_file = tmp_path / "user.json"
    user_file.write_text(json.dumps({"effortLevel": "low"}))  # no statusLine
    output = tmp_path / "out.json"

    cmd_apply_settings(
        _ns(
            hydra_template=str(hydra_tpl),
            user_template=str(user_tpl),
            user_file=str(user_file),
            output=str(output),
            hydra_url="http://h",
            hydra_repo_path="/r",
        )
    )

    out = json.loads(output.read_text())
    assert out["statusLine"] == {"type": "command", "command": "~/.claude/statusline.sh"}
    assert out["effortLevel"] == "low"  # user override still wins


def test_apply_preserves_existing_user_file(tmp_path: Path) -> None:
    hydra_tpl = tmp_path / "hydra.json"
    hydra_tpl.write_text(json.dumps({"hooks": {}, "effortLevel": "high"}))
    user_tpl = tmp_path / "user.template.json"
    user_tpl.write_text(json.dumps({"effortLevel": "xhigh"}))
    user_file = tmp_path / "user.json"
    user_file.write_text(json.dumps({"effortLevel": "low"}))
    output = tmp_path / "out.json"

    cmd_apply_settings(
        _ns(
            hydra_template=str(hydra_tpl),
            user_template=str(user_tpl),
            user_file=str(user_file),
            output=str(output),
            hydra_url="http://h",
            hydra_repo_path="/r",
        )
    )

    assert json.loads(user_file.read_text())["effortLevel"] == "low"
    assert json.loads(output.read_text())["effortLevel"] == "low"


def test_apply_migrates_old_format_user_file_on_disk(tmp_path: Path) -> None:
    """Regression: stale scaffolds (`max` + top-level defaultMode) must not pin
    the old behavior forever - they get migrated in place on the next run."""
    hydra_tpl = tmp_path / "hydra.json"
    hydra_tpl.write_text(json.dumps({"hooks": {}}))
    user_tpl = tmp_path / "user.template.json"
    user_tpl.write_text(
        json.dumps({"effortLevel": "xhigh", "permissions": {"defaultMode": "auto"}})
    )
    user_file = tmp_path / "user.json"
    user_file.write_text(json.dumps({"effortLevel": "max", "defaultMode": "auto"}))
    output = tmp_path / "out.json"

    cmd_apply_settings(
        _ns(
            hydra_template=str(hydra_tpl),
            user_template=str(user_tpl),
            user_file=str(user_file),
            output=str(output),
            hydra_url="http://h",
            hydra_repo_path="/r",
        )
    )

    # User file rewritten: stale max dropped, defaultMode wrapped in permissions.
    migrated_user = json.loads(user_file.read_text())
    assert migrated_user == {"permissions": {"defaultMode": "auto"}}
    # Output: template default flows through; no env promotion, no top-level defaultMode.
    out = json.loads(output.read_text())
    assert out["effortLevel"] == "xhigh"
    assert out["permissions"] == {"defaultMode": "auto"}
    assert "defaultMode" not in out
    assert "env" not in out


def test_apply_substitutes_placeholders_in_hydra_template(tmp_path: Path) -> None:
    hydra_tpl = tmp_path / "hydra.json"
    hydra_tpl.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "http", "url": "__HYDRA_URL__/api/x"}]}
                    ]
                }
            }
        )
    )
    user_tpl = tmp_path / "user.template.json"
    user_tpl.write_text(json.dumps({}))
    user_file = tmp_path / "user.json"
    output = tmp_path / "out.json"

    cmd_apply_settings(
        _ns(
            hydra_template=str(hydra_tpl),
            user_template=str(user_tpl),
            user_file=str(user_file),
            output=str(output),
            hydra_url="https://h.example",
            hydra_repo_path="/repo",
        )
    )

    out = json.loads(output.read_text())
    assert out["hooks"]["SessionStart"][0]["hooks"][0]["url"] == "https://h.example/api/x"


def test_apply_end_to_end_with_real_template(tmp_path: Path) -> None:
    """Verify the shipped Hydra + user templates merge into a valid settings.json."""
    repo = Path(__file__).resolve().parent.parent
    hydra_tpl = repo / "client" / "settings.json"
    user_tpl = repo / "client" / "settings.user.template.json"
    user_file = tmp_path / "user.json"
    output = tmp_path / "out.json"

    cmd_apply_settings(
        _ns(
            hydra_template=str(hydra_tpl),
            user_template=str(user_tpl),
            user_file=str(user_file),
            output=str(output),
            hydra_url="https://h.example",
            hydra_repo_path="/repo",
        )
    )

    out = json.loads(output.read_text())
    # Hydra hooks present
    assert "SessionStart" in out["hooks"]
    # User template keys present
    assert out["effortLevel"] == "xhigh"
    assert out["permissions"] == {"defaultMode": "auto"}
    assert "defaultMode" not in out  # only valid nested under permissions
    assert "env" not in out  # env-var promotion removed
    assert out["attribution"] == {"pr": "", "commit": ""}
    # statusLine has the shape Claude Code requires (rejects bare `{}`).
    assert out["statusLine"]["type"] == "command"
    assert out["statusLine"]["command"]
    # Placeholders substituted
    serialized = output.read_text()
    assert "__HYDRA_URL__" not in serialized
    assert "__HYDRA_REPO_PATH__" not in serialized
