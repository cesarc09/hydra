"""Tests for `hydra apply-settings` — merge Hydra template + user prefs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hydra_cli.apply_settings import cmd_apply_settings, merge, substitute


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


def _ns(**kwargs: str) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def test_apply_scaffolds_user_file_when_absent(tmp_path: Path) -> None:
    hydra_tpl = tmp_path / "hydra.json"
    hydra_tpl.write_text(json.dumps({"hooks": {}, "effortLevel": "high"}))
    user_tpl = tmp_path / "user.template.json"
    user_tpl.write_text(json.dumps({"effortLevel": "max"}))
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

    assert user_file.exists()
    assert json.loads(output.read_text())["effortLevel"] == "max"


def test_apply_preserves_existing_user_file(tmp_path: Path) -> None:
    hydra_tpl = tmp_path / "hydra.json"
    hydra_tpl.write_text(json.dumps({"hooks": {}, "effortLevel": "high"}))
    user_tpl = tmp_path / "user.template.json"
    user_tpl.write_text(json.dumps({"effortLevel": "max"}))
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
    assert out["effortLevel"] == "max"
    assert out["attribution"] == {"pr": "", "commit": ""}
    # Placeholders substituted
    serialized = output.read_text()
    assert "__HYDRA_URL__" not in serialized
    assert "__HYDRA_REPO_PATH__" not in serialized
