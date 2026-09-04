from __future__ import annotations

import json
from pathlib import Path

import pytest
from hydra_cli import __main__ as main_mod
from hydra_cli.author import author_fields


def _write_records(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


def test_claude_model_comes_from_newest_real_assistant(tmp_path: Path):
    root = tmp_path / "claude"
    _write_records(
        root / "project" / "claude-sid.jsonl",
        [
            {"type": "assistant", "message": {"model": "older-model"}},
            {"type": "assistant", "message": {"model": "newest-real"}},
            {"type": "assistant", "message": {"model": "<synthetic>"}},
        ],
    )

    fields = author_fields(
        {"CLAUDE_CODE_SESSION_ID": "claude-sid"},
        claude_root=root,
        codex_root=tmp_path / "codex",
        model=None,
    )

    assert fields == {
        "author_harness": "claude-code",
        "author_session_id": "claude-sid",
        "author_model": "newest-real",
    }


def test_codex_model_comes_from_newest_turn_context(tmp_path: Path):
    root = tmp_path / "codex"
    _write_records(
        root / "2026" / "09" / "04" / "rollout-test-codex-sid.jsonl",
        [
            {"type": "turn_context", "payload": {"model": "older-model"}},
            {"type": "event_msg", "payload": {"model": "ignored"}},
            {"type": "turn_context", "payload": {"model": "newest-model"}},
        ],
    )

    fields = author_fields(
        {"CODEX_SESSION_ID": "codex-sid"},
        claude_root=tmp_path / "claude",
        codex_root=root,
        model=None,
    )

    assert fields == {
        "author_harness": "codex-cli",
        "author_session_id": "codex-sid",
        "author_model": "newest-model",
    }


def test_session_without_transcript_has_no_model(tmp_path: Path):
    fields = author_fields(
        {"CLAUDE_CODE_SESSION_ID": "missing"},
        claude_root=tmp_path / "claude",
        codex_root=tmp_path / "codex",
        model=None,
    )

    assert fields == {
        "author_harness": "claude-code",
        "author_session_id": "missing",
        "author_model": None,
    }


def test_no_session_has_no_authorship(tmp_path: Path):
    fields = author_fields(
        {},
        claude_root=tmp_path / "claude",
        codex_root=tmp_path / "codex",
        model="ignored-without-a-session",
    )

    assert fields == {
        "author_harness": None,
        "author_session_id": None,
        "author_model": None,
    }


def test_model_override_wins(tmp_path: Path):
    root = tmp_path / "claude"
    _write_records(
        root / "project" / "sid.jsonl",
        [{"type": "assistant", "message": {"model": "transcript-model"}}],
    )

    fields = author_fields(
        {"CLAUDE_CODE_SESSION_ID": "sid"},
        claude_root=root,
        codex_root=tmp_path / "codex",
        model="override-model",
    )

    assert fields["author_model"] == "override-model"


def test_memory_create_sends_author_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    seen: dict[str, object] = {}

    def post(path: str, payload: dict[str, object]) -> tuple[int, str]:
        seen["path"] = path
        seen["payload"] = payload
        return 200, json.dumps(payload)

    monkeypatch.setattr(main_mod.api, "post", post)
    monkeypatch.setattr(main_mod, "_read_body", lambda args: "")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "cli-session")
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    args = main_mod.build_parser().parse_args([
        "memory", "create", "--name", "authored", "--type", "user",
        "--model", "x",
    ])

    main_mod.cmd_memory_create(args)
    capsys.readouterr()

    assert seen["path"] == "/api/memory"
    assert seen["payload"] == {
        "name": "authored",
        "type": "user",
        "author_harness": "claude-code",
        "author_session_id": "cli-session",
        "author_model": "x",
    }
