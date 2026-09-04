from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

TAIL_BYTES = 256 * 1024


def _tail_records(path: Path, limit: int = TAIL_BYTES) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            chunk = fh.read()
    except OSError:
        return []
    if size > limit:
        _, _, chunk = chunk.partition(b"\n")
    records = []
    for line in chunk.splitlines():
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _claude_model(record: dict[str, Any]) -> str | None:
    if record.get("type") != "assistant":
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    model = message.get("model")
    if isinstance(model, str) and model and model != "<synthetic>":
        return model
    return None


def _codex_model(record: dict[str, Any]) -> str | None:
    if record.get("type") != "turn_context":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    model = payload.get("model")
    return model if isinstance(model, str) and model else None


def _newest_model(
    paths: Iterable[Path], extract: Callable[[dict[str, Any]], str | None]
) -> str | None:
    candidates: list[tuple[int, str]] = []
    for path in paths:
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            continue
        for record in reversed(_tail_records(path)):
            model = extract(record)
            if model is not None:
                candidates.append((modified, model))
                break
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def author_fields(
    env: Mapping[str, str],
    *,
    claude_root: Path,
    codex_root: Path,
    model: str | None,
) -> dict[str, str | None]:
    claude_session = env.get("CLAUDE_CODE_SESSION_ID")
    codex_session = env.get("CODEX_SESSION_ID")
    if claude_session:
        if model is None:
            model = _newest_model(
                claude_root.glob(f"*/{claude_session}.jsonl"), _claude_model
            )
        return {
            "author_harness": "claude-code",
            "author_session_id": claude_session,
            "author_model": model,
        }
    if codex_session:
        if model is None:
            model = _newest_model(
                codex_root.glob(f"**/rollout-*-{codex_session}.jsonl"), _codex_model
            )
        return {
            "author_harness": "codex-cli",
            "author_session_id": codex_session,
            "author_model": model,
        }
    return {
        "author_harness": None,
        "author_session_id": None,
        "author_model": None,
    }
