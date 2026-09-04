"""Report Codex rollout token usage to Hydra."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from hydra_cli import api
from hydra_cli.usage import CHUNK, state_dir

_STATE_NAME = "codex-sweep.json"
_LONG_CONTEXT = 272_000
_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
_THREAD_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$",
    re.IGNORECASE,
)


@dataclass
class ParseResult:
    rows: list[dict[str, Any]]
    offset: int
    session_id: str | None
    usage_events: int = 0
    skipped_without_turn: int = 0
    long_context_calls: int = 0


def _state_path() -> Path:
    return state_dir() / _STATE_NAME


def _load_offsets() -> dict[str, int]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, int) and value >= 0
    }


def _save_offsets(offsets: dict[str, int]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(
            json.dumps(offsets, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _thread_id(path: Path) -> str | None:
    match = _THREAD_RE.search(path.name)
    return match.group(1) if match else None


def _record(raw: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {key: 0 for key in _USAGE_KEYS}
    return {key: int(value.get(key) or 0) for key in _USAGE_KEYS}


def _agent_type(source: Any) -> str | None:
    if not isinstance(source, dict):
        return None
    subagent = source.get("subagent")
    if subagent == "review":
        return "review"
    if not isinstance(subagent, dict):
        return None
    if subagent.get("other") == "guardian":
        return "guardian"
    if isinstance(subagent.get("thread_spawn"), dict):
        return "spawn"
    return None


def _parent_model_at(path: Path, cutoff: str) -> str | None:
    model = None
    try:
        with path.open("rb") as handle:
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break
                rec = _record(raw)
                if not rec or rec.get("type") != "turn_context":
                    continue
                timestamp = rec.get("timestamp")
                payload = rec.get("payload")
                if not isinstance(timestamp, str) or timestamp > cutoff:
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("model"), str):
                    model = payload["model"]
    except OSError:
        return None
    return model


def parse_file(
    path: str,
    offset: int = 0,
    *,
    thread_paths: dict[str, Path] | None = None,
) -> ParseResult:
    """Rebuild rollout state from byte zero and emit complete records after offset."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ParseResult([], offset, None)
    emit_from = 0 if size < offset else offset

    rows: list[dict[str, Any]] = []
    pos = 0
    session_id = None
    thread_id = None
    parent_thread_id = None
    source: Any = None
    meta_cwd = None
    meta_timestamp = None
    model = None
    effort = None
    cwd = None
    previous: dict[str, int] | None = None
    inherited_parent_model: str | None = None
    parent_model_checked = False
    usage_events = 0
    skipped_without_turn = 0
    long_context_calls = 0

    try:
        with open(path, "rb") as handle:
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break
                record_start = pos
                pos += len(raw)
                rec = _record(raw)
                if not rec:
                    continue
                payload = rec.get("payload")
                if not isinstance(payload, dict):
                    continue

                if rec.get("type") == "session_meta":
                    if session_id is None:
                        session_id = payload.get("session_id")
                        thread_id = payload.get("id")
                        parent_thread_id = payload.get("parent_thread_id")
                        source = payload.get("source")
                        meta_cwd = payload.get("cwd")
                        meta_timestamp = rec.get("timestamp")
                    continue

                if rec.get("type") == "turn_context":
                    model = payload.get("model")
                    effort = payload.get("effort")
                    cwd = payload.get("cwd")
                    continue

                if rec.get("type") != "event_msg" or payload.get("type") != "token_count":
                    continue
                usage_events += 1
                info = payload.get("info")
                if not isinstance(info, dict):
                    continue
                cumulative = _usage(info.get("total_token_usage"))
                last = _usage(info.get("last_token_usage"))
                if previous is None:
                    delta = cumulative
                elif any(cumulative[key] < previous[key] for key in _USAGE_KEYS):
                    delta = last
                else:
                    delta = {
                        key: cumulative[key] - previous[key] for key in _USAGE_KEYS
                    }
                previous = cumulative

                if record_start < emit_from or delta["total_tokens"] == 0:
                    continue
                if not isinstance(model, str) or not model:
                    skipped_without_turn += 1
                    continue
                timestamp = rec.get("timestamp")
                if not all(isinstance(v, str) and v for v in (session_id, thread_id, timestamp)):
                    continue

                row_model = model
                if (
                    row_model == "codex-auto-review"
                    and isinstance(parent_thread_id, str)
                    and isinstance(meta_timestamp, str)
                    and thread_paths is not None
                ):
                    if not parent_model_checked:
                        parent = thread_paths.get(parent_thread_id)
                        inherited_parent_model = (
                            _parent_model_at(parent, meta_timestamp) if parent else None
                        )
                        parent_model_checked = True
                    if inherited_parent_model:
                        row_model = inherited_parent_model

                window = int(info.get("model_context_window") or 0)
                if delta["input_tokens"] > _LONG_CONTEXT or window > _LONG_CONTEXT:
                    long_context_calls += 1
                rows.append(
                    {
                        "message_id": (
                            f"codex:{thread_id}:{timestamp}:{cumulative['total_tokens']}"
                        ),
                        "ts": timestamp,
                        "model": row_model,
                        "harness": "codex-cli",
                        "cwd": cwd if isinstance(cwd, str) else meta_cwd,
                        "effort": effort,
                        "is_subagent": parent_thread_id is not None,
                        "agent_type": _agent_type(source),
                        "service_tier": None,
                        "speed": None,
                        "input_tokens": (
                            delta["input_tokens"] - delta["cached_input_tokens"]
                        ),
                        "output_tokens": delta["output_tokens"],
                        "cache_read_tokens": delta["cached_input_tokens"],
                        "cache_write_5m_tokens": delta["cache_write_input_tokens"],
                        "cache_write_1h_tokens": 0,
                        "web_search_requests": 0,
                        "web_fetch_requests": 0,
                    }
                )
    except OSError as exc:
        print(f"hydra usage sweep: cannot read {path}: {exc}", file=sys.stderr)
        return ParseResult([], offset, None)

    return ParseResult(
        rows,
        pos,
        session_id if isinstance(session_id, str) else None,
        usage_events,
        skipped_without_turn,
        long_context_calls,
    )


def _handshake() -> bool:
    query = urlencode(
        {"group_by": "harness", "since": datetime.now(UTC).isoformat()}
    )
    status, _body = api.get(f"/api/usage/summary?{query}")
    if status == 200:
        return True
    print(
        f"hydra usage sweep: server does not support harness usage ({status}); no rows sent",
        file=sys.stderr,
    )
    return False


def run_sweep(root: str | None = None, *, reset: bool = False) -> int:
    base = Path(root) if root else Path.home() / ".codex" / "sessions"
    if not base.is_dir():
        print(f"hydra usage sweep: no rollout root at {base}", file=sys.stderr)
        return 1

    paths = sorted(base.rglob("rollout-*.jsonl"))
    thread_paths = {
        thread_id: path
        for path in paths
        if (thread_id := _thread_id(path)) is not None
    }
    offsets = {} if reset else _load_offsets()
    pending: dict[str, int] = {}
    batches: dict[str, list[dict[str, Any]]] = {}
    scanned = 0
    no_usage = 0
    skipped_without_turn = 0
    long_context_calls = 0

    for path in paths:
        path_str = str(path)
        old_offset = offsets.get(path_str, 0)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == old_offset:
            pending[path_str] = old_offset
            continue

        scanned += 1
        result = parse_file(path_str, old_offset, thread_paths=thread_paths)
        pending[path_str] = result.offset
        no_usage += result.usage_events == 0
        skipped_without_turn += result.skipped_without_turn
        long_context_calls += result.long_context_calls
        if result.session_id and result.rows:
            batches.setdefault(result.session_id, []).extend(result.rows)

    rows = sum(len(messages) for messages in batches.values())
    if rows and not _handshake():
        return 0

    for session_id, messages in batches.items():
        for start in range(0, len(messages), CHUNK):
            status, body = api.post(
                "/api/usage/messages",
                {"session_id": session_id, "messages": messages[start : start + CHUNK]},
            )
            if status not in (200, 204):
                print(
                    f"hydra usage sweep: POST failed ({status}): {body}",
                    file=sys.stderr,
                )
                return 1

    try:
        _save_offsets(pending)
    except OSError as exc:
        print(f"hydra usage sweep: cannot save state: {exc}", file=sys.stderr)
        return 1
    print(
        f"hydra usage sweep: {rows} rows from {scanned} changed files;"
        f" {no_usage} files with no usage events;"
        f" {skipped_without_turn} events before turn context;"
        f" {long_context_calls} long-context calls",
        file=sys.stderr,
    )
    return 0


def cmd_sweep(args: Any) -> int:
    return run_sweep(args.root, reset=args.reset)
