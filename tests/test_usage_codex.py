"""Codex rollout parsing and sweep tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from hydra_cli import usage_codex

FIXTURES = Path(__file__).parent / "fixtures"
PARENT = "11111111-1111-4111-8111-111111111111"
GUARDIAN = "22222222-2222-4222-8222-222222222222"
REVIEW = "33333333-3333-4333-8333-333333333333"
SPAWN = "44444444-4444-4444-8444-444444444444"
TURNLESS = "55555555-5555-4555-8555-555555555555"


def _fixture(name: str) -> Path:
    return FIXTURES / f"codex_{name}.jsonl"


def _paths() -> dict[str, Path]:
    return {
        PARENT: _fixture("parent"),
        GUARDIAN: _fixture("guardian"),
        REVIEW: _fixture("review"),
        SPAWN: _fixture("spawn"),
    }


def _install(root: Path, name: str, thread_id: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"rollout-2026-01-01T00-00-00-{thread_id}.jsonl"
    shutil.copyfile(_fixture(name), path)
    return path


def _record_rollout(thread_id: str, session_id: str, count: int) -> str:
    records = [
        {
            "timestamp": "2026-02-01T00:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": thread_id,
                "session_id": session_id,
                "parent_thread_id": None,
                "source": "exec",
                "cwd": "/project",
            },
        },
        {
            "timestamp": "2026-02-01T00:00:01Z",
            "type": "turn_context",
            "payload": {"model": "gpt-5.6-sol", "effort": "high", "cwd": "/project"},
        },
    ]
    for i in range(1, count + 1):
        usage = {
            "input_tokens": i,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
            "total_tokens": i,
        }
        records.append(
            {
                "timestamp": f"2026-02-01T00:{i // 60:02d}:{i % 60:02d}Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": usage,
                        "last_token_usage": usage,
                        "model_context_window": 258400,
                    },
                },
            }
        )
    return "".join(json.dumps(record) + "\n" for record in records)


class FakeApi:
    def __init__(self, *, handshake: int = 200, posts: list[int] | None = None):
        self.handshake = handshake
        self.post_statuses = iter(posts or [])
        self.gets: list[str] = []
        self.batches: list[dict] = []

    def get(self, path: str) -> tuple[int, str]:
        self.gets.append(path)
        return self.handshake, "{}"

    def post(self, path: str, payload: dict) -> tuple[int, str]:
        assert path == "/api/usage/messages"
        self.batches.append(payload)
        return next(self.post_statuses, 200), "response"


@pytest.fixture
def sweep_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "sessions"
    state = tmp_path / "state"
    fake = FakeApi()
    monkeypatch.setattr(usage_codex, "state_dir", lambda: state)
    monkeypatch.setattr(usage_codex.api, "get", fake.get)
    monkeypatch.setattr(usage_codex.api, "post", fake.post)
    return root, state, fake


def test_parent_parser_diffs_duplicates_and_resets_deterministically():
    first = usage_codex.parse_file(str(_fixture("parent")))
    second = usage_codex.parse_file(str(_fixture("parent")))

    assert first.rows == second.rows
    assert len(first.rows) == 3
    assert len({row["message_id"] for row in first.rows}) == 3
    assert first.usage_events == 4
    assert first.rows[0]["input_tokens"] == 40
    assert first.rows[0]["cache_read_tokens"] == 60
    assert first.rows[1]["input_tokens"] == 20
    assert first.rows[2]["input_tokens"] == 20
    assert first.rows[2]["cache_read_tokens"] == 10
    assert first.rows[2]["output_tokens"] == 5


def test_resume_rebuilds_context_but_emits_only_rows_after_offset():
    path = _fixture("parent")
    lines = path.read_bytes().splitlines(keepends=True)
    offset = sum(len(line) for line in lines[:4])
    cold = usage_codex.parse_file(str(path))
    resumed = usage_codex.parse_file(str(path), offset)

    assert [row["message_id"] for row in resumed.rows] == [
        row["message_id"] for row in cold.rows[1:]
    ]
    assert resumed.offset == cold.offset


def test_guardian_inherits_parent_model_and_falls_back_without_parent():
    inherited = usage_codex.parse_file(
        str(_fixture("guardian")), thread_paths=_paths()
    ).rows[0]
    fallback = usage_codex.parse_file(
        str(_fixture("guardian")), thread_paths={GUARDIAN: _fixture("guardian")}
    ).rows[0]

    assert inherited["model"] == "gpt-5.6-sol"
    assert fallback["model"] == "codex-auto-review"
    assert inherited["is_subagent"] is True
    assert inherited["agent_type"] == "guardian"
    assert inherited["cache_write_5m_tokens"] == 3


@pytest.mark.parametrize(
    "name,agent_type,thread_id",
    [("review", "review", REVIEW), ("spawn", "spawn", SPAWN)],
)
def test_child_types_and_second_session_meta(name: str, agent_type: str, thread_id: str):
    parsed = usage_codex.parse_file(str(_fixture(name)))
    assert parsed.session_id == PARENT
    assert parsed.rows[0]["is_subagent"] is True
    assert parsed.rows[0]["agent_type"] == agent_type
    assert parsed.rows[0]["message_id"].startswith(f"codex:{thread_id}:")


def test_turnless_exec_fixture_has_no_usage():
    parsed = usage_codex.parse_file(str(_fixture("turnless_exec")))
    assert parsed.rows == []
    assert parsed.usage_events == 0


def test_turnless_exec_sweep_reports_file_without_post(sweep_env, capsys):
    root, _state, fake = sweep_env
    _install(root, "turnless_exec", TURNLESS)

    assert usage_codex.run_sweep(str(root)) == 0
    assert "1 files with no usage events" in capsys.readouterr().err
    assert fake.batches == []


def test_events_before_turn_are_skipped_and_long_context_is_counted(tmp_path: Path):
    thread_id = "88888888-8888-4888-8888-888888888888"
    path = tmp_path / f"rollout-2026-01-01T00-00-00-{thread_id}.jsonl"
    usage_a = {
        "input_tokens": 5,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 5,
    }
    usage_b = dict(usage_a, input_tokens=6, total_tokens=6)
    records = [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": thread_id, "session_id": thread_id, "cwd": "/project"},
        },
        {
            "timestamp": "2026-01-01T00:00:01Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": usage_a, "last_token_usage": usage_a},
            },
        },
        {
            "timestamp": "2026-01-01T00:00:02Z",
            "type": "turn_context",
            "payload": {"model": "gpt-5.6-sol", "cwd": "/project"},
        },
        {
            "timestamp": "2026-01-01T00:00:03Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": usage_b,
                    "last_token_usage": usage_b,
                    "model_context_window": 300000,
                },
            },
        },
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))

    parsed = usage_codex.parse_file(str(path))
    assert len(parsed.rows) == 1
    assert parsed.skipped_without_turn == 1
    assert parsed.long_context_calls == 1


def test_corpus_canary_uses_independent_literal_denominator():
    fixtures = sorted(FIXTURES.glob("codex_*.jsonl"))
    literal_events = sum(
        path.read_bytes().count(b'"type":"token_count"') for path in fixtures
    )
    parser_events = sum(usage_codex.parse_file(str(path)).usage_events for path in fixtures)
    assert parser_events == literal_events == 7

    parent = usage_codex.parse_file(str(_fixture("parent"))).rows
    billed_tokens = sum(
        row["input_tokens"] + row["cache_read_tokens"] + row["output_tokens"]
        for row in parent
    )
    assert billed_tokens == 180 + 35


def test_partial_line_is_re_read_on_next_sweep(sweep_env):
    root, _state, fake = sweep_env
    path = _install(root, "parent", PARENT)
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:3]) + lines[4][:40])

    assert usage_codex.run_sweep(str(root)) == 0
    assert sum(len(batch["messages"]) for batch in fake.batches) == 1

    path.write_bytes(b"".join(lines))
    fake.batches.clear()
    assert usage_codex.run_sweep(str(root)) == 0
    assert sum(len(batch["messages"]) for batch in fake.batches) == 2


def test_truncated_rollout_restarts_at_zero(sweep_env):
    root, _state, fake = sweep_env
    path = _install(root, "parent", PARENT)
    assert usage_codex.run_sweep(str(root)) == 0

    path.write_text(_record_rollout(PARENT, PARENT, 1), encoding="utf-8")
    fake.batches.clear()
    assert usage_codex.run_sweep(str(root)) == 0
    assert sum(len(batch["messages"]) for batch in fake.batches) == 1


def test_unchanged_rollout_is_not_parsed(sweep_env, monkeypatch: pytest.MonkeyPatch):
    root, _state, fake = sweep_env
    _install(root, "parent", PARENT)
    assert usage_codex.run_sweep(str(root)) == 0
    fake.batches.clear()

    def fail(*_args, **_kwargs):
        raise AssertionError("unchanged file was opened")

    monkeypatch.setattr(usage_codex, "parse_file", fail)
    assert usage_codex.run_sweep(str(root)) == 0
    assert fake.batches == []


def test_handshake_failure_exits_zero_without_post_or_state_change(
    sweep_env, monkeypatch: pytest.MonkeyPatch
):
    root, state, _fake = sweep_env
    _install(root, "parent", PARENT)
    state.mkdir()
    state_path = state / "codex-sweep.json"
    state_path.write_bytes(b'{"old": 3}\n')
    before = state_path.read_bytes()
    fake = FakeApi(handshake=422)
    monkeypatch.setattr(usage_codex.api, "get", fake.get)
    monkeypatch.setattr(usage_codex.api, "post", fake.post)

    assert usage_codex.run_sweep(str(root), reset=True) == 0
    assert len(fake.gets) == 1
    assert "group_by=harness" in fake.gets[0]
    assert fake.batches == []
    assert state_path.read_bytes() == before


@pytest.mark.parametrize("statuses", [[500], [200, 500]])
def test_post_failure_leaves_state_untouched(
    sweep_env, monkeypatch: pytest.MonkeyPatch, statuses: list[int]
):
    root, state, _fake = sweep_env
    root.mkdir()
    path = root / (
        "rollout-2026-02-01T00-00-00-66666666-6666-4666-8666-666666666666.jsonl"
    )
    count = 501 if len(statuses) == 2 else 1
    path.write_text(
        _record_rollout(
            "66666666-6666-4666-8666-666666666666",
            "66666666-6666-4666-8666-666666666666",
            count,
        ),
        encoding="utf-8",
    )
    state.mkdir()
    state_path = state / "codex-sweep.json"
    state_path.write_bytes(b'{"old": 3}\n')
    before = state_path.read_bytes()
    fake = FakeApi(posts=statuses)
    monkeypatch.setattr(usage_codex.api, "get", fake.get)
    monkeypatch.setattr(usage_codex.api, "post", fake.post)

    assert usage_codex.run_sweep(str(root), reset=True) == 1
    assert len(fake.batches) == len(statuses)
    assert state_path.read_bytes() == before


def test_unparseable_state_triggers_full_rescan(sweep_env):
    root, state, fake = sweep_env
    _install(root, "parent", PARENT)
    state.mkdir()
    (state / "codex-sweep.json").write_text("{truncated", encoding="utf-8")

    assert usage_codex.run_sweep(str(root)) == 0
    assert sum(len(batch["messages"]) for batch in fake.batches) == 3


def test_batches_are_chunked_and_grouped_by_root_session(
    sweep_env, monkeypatch: pytest.MonkeyPatch
):
    root, _state, fake = sweep_env
    root.mkdir()
    first = "66666666-6666-4666-8666-666666666666"
    second = "77777777-7777-4777-8777-777777777777"
    (root / f"rollout-2026-02-01T00-00-00-{first}.jsonl").write_text(
        _record_rollout(first, first, 501), encoding="utf-8"
    )
    (root / f"rollout-2026-02-01T00-00-00-{second}.jsonl").write_text(
        _record_rollout(second, second, 1), encoding="utf-8"
    )
    guardian = _install(root, "guardian", GUARDIAN)
    guardian.write_text(
        guardian.read_text().replace(PARENT, first), encoding="utf-8"
    )
    monkeypatch.setattr(usage_codex, "CHUNK", 500)

    assert usage_codex.run_sweep(str(root)) == 0
    sizes = sorted(len(batch["messages"]) for batch in fake.batches)
    assert sizes == [1, 2, 500]
    for batch in fake.batches:
        expected_threads = {first, GUARDIAN} if batch["session_id"] == first else {second}
        assert all(
            message["message_id"].split(":", 2)[1] in expected_threads
            for message in batch["messages"]
        )
