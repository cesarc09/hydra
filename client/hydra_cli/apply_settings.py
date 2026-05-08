"""Compose ~/.claude/settings.json from the Hydra template + user prefs.

For each event under `hooks`, Hydra's matcher-groups come first and the user's
groups append. For all other top-level keys (effortLevel, attribution, etc.)
the user's value wins. The user file is scaffolded from the shipped template
on first run and never overwritten afterwards.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


def substitute(text: str, hydra_url: str, hydra_repo_path: str) -> str:
    return text.replace("__HYDRA_URL__", hydra_url).replace(
        "__HYDRA_REPO_PATH__", hydra_repo_path
    )


def merge(hydra: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    """Hooks: per-event arrays concatenate (Hydra first). Other keys: user wins."""
    result: dict[str, Any] = dict(hydra)
    for key, user_val in user.items():
        if key == "hooks" and isinstance(user_val, dict):
            merged_hooks: dict[str, Any] = dict(hydra.get("hooks") or {})
            for event_name, user_groups in user_val.items():
                hydra_groups = merged_hooks.get(event_name) or []
                if isinstance(user_groups, list) and isinstance(hydra_groups, list):
                    merged_hooks[event_name] = list(hydra_groups) + list(user_groups)
                else:
                    merged_hooks[event_name] = user_groups
            result["hooks"] = merged_hooks
        else:
            result[key] = user_val
    return result


def cmd_apply_settings(args: argparse.Namespace) -> None:
    hydra_template = Path(args.hydra_template)
    user_template = Path(args.user_template)
    user_file = Path(args.user_file)
    output = Path(args.output)

    if not user_file.exists():
        user_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(user_template, user_file)
        print(f"Scaffolded {user_file} from {user_template}", file=sys.stderr)

    raw = hydra_template.read_text(encoding="utf-8")
    raw = substitute(raw, args.hydra_url, args.hydra_repo_path)
    hydra = json.loads(raw)
    user = json.loads(user_file.read_text(encoding="utf-8"))

    merged = merge(hydra, user)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
