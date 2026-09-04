"""Pull server-distributed instructions and skills into coding harnesses."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from hydra_cli import api

HARNESSES = ("claude-code", "codex-cli")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def claude_dir() -> Path:
    return Path.home() / ".claude"


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def agents_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


def instructions_path(harness: str) -> Path:
    if harness == "claude-code":
        return claude_dir() / "CLAUDE.md"
    if harness == "codex-cli":
        return codex_home() / "AGENTS.md"
    raise ValueError(f"unsupported harness: {harness}")


def skills_dir(harness: str) -> Path:
    if harness == "claude-code":
        return claude_dir() / "skills"
    if harness == "codex-cli":
        return agents_skills_dir()
    raise ValueError(f"unsupported harness: {harness}")


def state_path(harness: str) -> Path:
    return claude_dir() / f".hydra-skills-{harness}.json"


def _load_managed(harness: str) -> set[Path]:
    try:
        data = json.loads(state_path(harness).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    managed = data.get("managed", []) if isinstance(data, dict) else []
    return {Path(path) for path in managed if isinstance(path, str) and Path(path).is_absolute()}


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_bytes(content)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _save_managed(harness: str, managed: set[Path]) -> None:
    content = (
        json.dumps({"managed": sorted(str(path.absolute()) for path in managed)}, indent=2)
        + "\n"
    ).encode()
    _atomic_write(state_path(harness), content)


def _applies_here(instances: Any) -> bool:
    if not instances or not isinstance(instances, list):
        return True
    return os.environ.get("HYDRA_INSTANCE_ID", "").strip() in instances


def _frontmatter(text: str) -> tuple[list[str], int] | None:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None
    for index, line in enumerate(lines[1:], 1):
        if line.rstrip("\r\n") == "---":
            return lines, index
    return None


def _claude_skill(text: str, *, implicit: bool, path: Path) -> bytes:
    parsed = _frontmatter(text)
    if parsed is None:
        if not implicit:
            print(
                f"warning: {path} has no YAML frontmatter; cannot disable implicit invocation",
                file=sys.stderr,
            )
        return text.encode()
    lines, end = parsed
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    # The server's implicit_invocation is authoritative, so an existing key is
    # rewritten rather than kept: a rendered SKILL.md pasted back into common.md
    # carries the old value, and honouring it would silently invert the policy.
    kept = [
        line
        for line in lines[1:end]
        if line.partition(":")[0].strip() != "disable-model-invocation"
    ]
    if not implicit:
        kept.append(f"disable-model-invocation: true{newline}")
    return "".join([lines[0], *kept, *lines[end:]]).encode()


def _frontmatter_fields(text: str) -> dict[str, str]:
    parsed = _frontmatter(text)
    if parsed is None:
        return {}
    lines, end = parsed
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if separator:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            fields[key.strip()] = value
    return fields


def _quoted(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _openai_yaml(name: str, text: str, *, implicit: bool) -> bytes:
    fields = _frontmatter_fields(text)
    display_name = fields.get("name") or name
    description = fields.get("description", "")
    rendered = (
        "interface:\n"
        f'  display_name: "{_quoted(display_name)}"\n'
        f'  short_description: "{_quoted(description)}"\n'
        "\n"
        "policy:\n"
        f"  allow_implicit_invocation: {str(implicit).lower()}\n"
    )
    return rendered.encode()


def _targets(harness: str, name: str, spec: dict[str, Any]) -> list[tuple[Path, bytes]]:
    kind = spec.get("kind")
    files = spec.get("files")
    implicit = spec.get("implicit_invocation", False)
    if not isinstance(files, dict) or not isinstance(implicit, bool):
        raise ValueError(f"malformed skill payload: {name}")
    if kind == "instructions":
        text = files.get("instructions")
        if not isinstance(text, str):
            raise ValueError(f"malformed skill payload: {name}")
        return [(instructions_path(harness), text.encode())]
    if kind != "skill":
        raise ValueError(f"malformed skill payload: {name}")
    text = files.get("SKILL.md")
    if not isinstance(text, str):
        raise ValueError(f"malformed skill payload: {name}")
    root = skills_dir(harness) / name
    if harness == "claude-code":
        return [(root / "SKILL.md", _claude_skill(text, implicit=implicit, path=root / "SKILL.md"))]
    return [
        (root / "SKILL.md", text.encode()),
        (root / "agents" / "openai.yaml", _openai_yaml(name, text, implicit=implicit)),
    ]


def _install(path: Path, content: bytes, previously: set[Path], *, adopt: bool) -> str:
    path = path.absolute()
    if path.is_symlink():
        print(f"refused: {path} (symlink)", file=sys.stderr)
        return "refused"
    if path.exists() and not path.is_file():
        print(f"refused: {path} (not a regular file)", file=sys.stderr)
        return "refused"
    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        existing = None
    except OSError:
        print(f"refused: {path} (not a regular file)", file=sys.stderr)
        return "refused"
    if existing == content:
        return "unchanged"
    if existing is not None and path not in previously and not adopt:
        print(
            f"refused: {path} (unmanaged; rerun with --adopt to take ownership)",
            file=sys.stderr,
        )
        return "refused"
    _atomic_write(path, content)
    return "written"


def _remove_empty_skill_dirs(harness: str, path: Path) -> None:
    root = skills_dir(harness).absolute()
    parent = path.parent
    while parent != root.parent and parent != root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def run_pull(harness: str, *, adopt: bool = False) -> int:
    """Pull all rendered skills for one harness and prune managed skill files."""
    if harness not in HARNESSES:
        raise ValueError(f"unsupported harness: {harness}")
    status, body = api.get(f"/api/config/skills/{harness}")
    if status != 200:
        print(f"  skills pull [{harness}] failed ({status}): {body}", file=sys.stderr)
        return 1
    try:
        served = json.loads(body)
    except json.JSONDecodeError:
        print(f"  skills pull [{harness}] failed: invalid JSON from server", file=sys.stderr)
        return 1
    if not isinstance(served, dict):
        print(f"  skills pull [{harness}] failed: unexpected payload shape", file=sys.stderr)
        return 1

    previously = {path.absolute() for path in _load_managed(harness)}
    if not served:
        print(
            f"  skills pull [{harness}]: server served 0 skills, keeping "
            f"{len(previously)} local file(s) - nothing pruned",
            file=sys.stderr,
        )
        print(f"  skills pull [{harness}]: 0 written, 0 unchanged, 0 pruned, 0 refused")
        return 0

    selected: list[tuple[str, dict[str, Any]]] = []
    try:
        for name, spec in served.items():
            if (
                not isinstance(name, str)
                or not _NAME_RE.fullmatch(name)
                or not isinstance(spec, dict)
            ):
                raise ValueError(f"malformed skill payload: {name!r}")
            if not spec.get("enabled", True) or not _applies_here(spec.get("instances")):
                continue
            selected.append((name, spec))
        rendered = [(name, _targets(harness, name, spec)) for name, spec in selected]
    except ValueError as exc:
        print(f"  skills pull [{harness}] failed: {exc}", file=sys.stderr)
        return 1

    instruction = instructions_path(harness).absolute()
    managed = {instruction} & previously
    refused_paths: set[Path] = set()
    counts = {"written": 0, "unchanged": 0, "pruned": 0, "refused": 0}
    for _name, targets in rendered:
        for path, content in targets:
            path = path.absolute()
            result = _install(path, content, previously, adopt=adopt)
            counts[result] += 1
            if result != "refused":
                managed.add(path)
            elif path == instruction:
                managed.discard(path)
            else:
                refused_paths.add(path)

    for path in sorted(previously - managed - refused_paths):
        if path == instruction:
            continue
        path.unlink(missing_ok=True)
        counts["pruned"] += 1
        _remove_empty_skill_dirs(harness, path)

    _save_managed(harness, managed)
    print(
        f"  skills pull [{harness}]: {counts['written']} written, "
        f"{counts['unchanged']} unchanged, {counts['pruned']} pruned, "
        f"{counts['refused']} refused"
    )
    return 1 if counts["refused"] else 0
