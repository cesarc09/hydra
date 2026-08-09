"""Tests for `hydra_cli usage report` - transcript parsing and byte offsets.

Exercises report_session against real-shaped transcript lines with a fake
api.post and a tmp state dir (no live server). The properties worth guarding:
one API message writes N assistant records but must produce ONE row; subagent
transcripts live in a sibling directory and are easy to miss entirely; and the
offset must never advance past a failed POST or into a partially-written line.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hydra_cli import usage as usage_mod

USAGE = {
    "input_tokens": 2,
    "cache_creation_input_tokens": 25850,
    "cache_read_input_tokens": 21262,
    "output_tokens": 1027,
    "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
    "service_tier": "standard",
    "cache_creation": {
        "ephemeral_1h_input_tokens": 25850,
        "ephemeral_5m_input_tokens": 0,
    },
    "speed": "standard",
}


def record(message_id: str, **over) -> str:
    """One assistant record, in the shape Claude Code actually writes."""
    rec = {
        "type": "assistant",
        "timestamp": "2026-08-09T14:44:02.651Z",
        "cwd": "/home/giosue/projects/hydra",
        "effort": "xhigh",
        "isSidechain": False,
        "sessionId": "s1",
        "message": {
            "id": message_id,
            "model": "claude-opus-5",
            "role": "assistant",
            "usage": dict(USAGE),
        },
    }
    for key, value in over.items():
        if key in ("model", "usage"):
            rec["message"][key] = value
        else:
            rec[key] = value
    return json.dumps(rec) + "\n"


class FakePost:
    def __init__(self) -> None:
        self.batches: list[dict] = []
        self.status = 200

    def post(self, path: str, payload: dict) -> tuple[int, str]:
        assert path == "/api/usage/messages"
        self.batches.append(payload)
        return self.status, "{}"

    @property
    def sent_ids(self) -> list[str]:
        return [m["message_id"] for b in self.batches for m in b["messages"]]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(usage_mod, "state_dir", lambda: tmp_path / "state")
    fake = FakePost()
    monkeypatch.setattr(usage_mod.api, "post", fake.post)
    projects = tmp_path / "projects" / "-home-giosue-projects-hydra"
    projects.mkdir(parents=True)
    return fake, projects


def test_repeated_records_collapse_to_one_row(env):
    """Claude Code writes one assistant record per content block, each repeating
    the same usage - summing records instead of messages inflated output tokens
    2.55x on a real session."""
    fake, proj = env
    transcript = proj / "s1.jsonl"
    transcript.write_text(record("msg_a") + record("msg_a") + record("msg_b"))

    assert usage_mod.report_session("s1", str(transcript)) == 0
    assert fake.sent_ids == ["msg_a", "msg_b"]
    assert fake.batches[0]["messages"][0]["output_tokens"] == 1027


def test_subagent_transcripts_are_included(env):
    """Subagent usage is not in the main transcript at all - it lives in
    <dir>/<session_id>/subagents/**."""
    fake, proj = env
    transcript = proj / "s1.jsonl"
    transcript.write_text(record("main_1"))
    subdir = proj / "s1" / "subagents"
    subdir.mkdir(parents=True)
    (subdir / "agent-abc.jsonl").write_text(
        record("sub_1", isSidechain=True, attributionAgent="Explore")
    )
    nested = subdir / "workflows" / "wf_1"
    nested.mkdir(parents=True)
    (nested / "agent-def.jsonl").write_text(
        record("sub_2", isSidechain=True, attributionAgent="general-purpose")
    )

    assert usage_mod.report_session("s1", str(transcript)) == 0
    assert sorted(fake.sent_ids) == ["main_1", "sub_1", "sub_2"]
    by_id = {m["message_id"]: m for b in fake.batches for m in b["messages"]}
    assert by_id["sub_1"]["is_subagent"] is True
    assert by_id["sub_1"]["agent_type"] == "Explore"
    assert by_id["main_1"]["is_subagent"] is False


def test_symlinked_workflow_dir_is_reported_by_its_owner_only(env):
    """Claude Code aliases a workflow's subagent dir into sessions that consume
    it. Following the link would report one workflow's agents under whichever
    session scanned first; the owning session covers them either way."""
    fake, proj = env
    owner = proj / "s_owner" / "subagents" / "workflows" / "wf_1"
    owner.mkdir(parents=True)
    (owner / "agent-a.jsonl").write_text(
        record("wf_msg", isSidechain=True, attributionAgent="workflow-subagent")
    )
    (proj / "s_owner.jsonl").write_text(record("owner_main"))

    consumer = proj / "s_user" / "subagents" / "workflows"
    consumer.mkdir(parents=True)
    (consumer / "wf_1").symlink_to(owner, target_is_directory=True)
    (proj / "s_user.jsonl").write_text(record("user_main"))

    usage_mod.report_session("s_user", str(proj / "s_user.jsonl"))
    assert fake.sent_ids == ["user_main"]

    fake.batches.clear()
    usage_mod.report_session("s_owner", str(proj / "s_owner.jsonl"))
    assert sorted(fake.sent_ids) == ["owner_main", "wf_msg"]


def test_second_run_sends_only_new_messages(env):
    fake, proj = env
    transcript = proj / "s1.jsonl"
    transcript.write_text(record("msg_a"))
    usage_mod.report_session("s1", str(transcript))

    with transcript.open("a") as handle:
        handle.write(record("msg_b"))
    fake.batches.clear()
    usage_mod.report_session("s1", str(transcript))
    assert fake.sent_ids == ["msg_b"]

    # Nothing new at all: no request is made.
    fake.batches.clear()
    usage_mod.report_session("s1", str(transcript))
    assert fake.batches == []


def test_failed_post_does_not_advance_the_offset(env):
    fake, proj = env
    transcript = proj / "s1.jsonl"
    transcript.write_text(record("msg_a"))

    fake.status = 500
    assert usage_mod.report_session("s1", str(transcript)) == 1

    fake.status = 200
    fake.batches.clear()
    assert usage_mod.report_session("s1", str(transcript)) == 0
    assert fake.sent_ids == ["msg_a"]


def test_partial_trailing_line_is_not_consumed(env):
    """A line Claude Code is still writing must not move the offset into the
    middle of a JSON object."""
    fake, proj = env
    transcript = proj / "s1.jsonl"
    complete = record("msg_a")
    transcript.write_text(complete + '{"type":"assistant","messa')

    usage_mod.report_session("s1", str(transcript))
    assert fake.sent_ids == ["msg_a"]

    # Finish the line; it is picked up next time, exactly once.
    transcript.write_text(complete + record("msg_b"))
    fake.batches.clear()
    usage_mod.report_session("s1", str(transcript))
    assert fake.sent_ids == ["msg_b"]


def test_truncated_file_resets_offset(env):
    fake, proj = env
    transcript = proj / "s1.jsonl"
    transcript.write_text(record("msg_a") + record("msg_b"))
    usage_mod.report_session("s1", str(transcript))

    transcript.write_text(record("msg_c"))  # replaced, now shorter
    fake.batches.clear()
    usage_mod.report_session("s1", str(transcript))
    assert fake.sent_ids == ["msg_c"]


def test_synthetic_and_untimed_records_are_skipped(env):
    fake, proj = env
    transcript = proj / "s1.jsonl"
    transcript.write_text(
        record("msg_synth", model="<synthetic>")
        + record("msg_no_ts", timestamp=None)
        + record("msg_ok")
    )
    usage_mod.report_session("s1", str(transcript))
    assert fake.sent_ids == ["msg_ok"]


def test_cache_split_falls_back_when_ttl_buckets_absent(env):
    fake, proj = env
    transcript = proj / "s1.jsonl"
    no_split = {k: v for k, v in USAGE.items() if k != "cache_creation"}
    transcript.write_text(record("msg_a", usage=no_split))

    usage_mod.report_session("s1", str(transcript))
    sent = fake.batches[0]["messages"][0]
    assert sent["cache_write_5m_tokens"] == 25850
    assert sent["cache_write_1h_tokens"] == 0


def test_zeroed_buckets_fall_back_to_the_reported_total(env):
    """Measured twice in 101 days: cache_creation_input_tokens is set while both
    TTL buckets read 0. The write must not be dropped."""
    fake, proj = env
    zeroed = dict(USAGE)
    zeroed["cache_creation_input_tokens"] = 5975
    zeroed["cache_creation"] = {
        "ephemeral_5m_input_tokens": 0,
        "ephemeral_1h_input_tokens": 0,
    }
    (proj / "s1.jsonl").write_text(record("msg_a", usage=zeroed))

    usage_mod.report_session("s1", str(proj / "s1.jsonl"))
    sent = fake.batches[0]["messages"][0]
    assert sent["cache_write_5m_tokens"] == 5975
    assert sent["cache_write_1h_tokens"] == 0


def test_buckets_win_when_the_reported_total_is_zero(env):
    """The mirror case, seen 11 times: the buckets carry the truth (they match
    the per-iteration figures) and the total is the broken field."""
    fake, proj = env
    broken_total = dict(USAGE)
    broken_total["cache_creation_input_tokens"] = 0
    (proj / "s1.jsonl").write_text(record("msg_a", usage=broken_total))

    usage_mod.report_session("s1", str(proj / "s1.jsonl"))
    sent = fake.batches[0]["messages"][0]
    assert sent["cache_write_1h_tokens"] == 25850
    assert sent["cache_write_5m_tokens"] == 0


def test_ttl_buckets_are_kept_separate(env):
    fake, proj = env
    transcript = proj / "s1.jsonl"
    transcript.write_text(record("msg_a"))
    usage_mod.report_session("s1", str(transcript))
    sent = fake.batches[0]["messages"][0]
    assert sent["cache_write_1h_tokens"] == 25850
    assert sent["cache_write_5m_tokens"] == 0


def test_batches_are_chunked(env, monkeypatch):
    fake, proj = env
    monkeypatch.setattr(usage_mod, "CHUNK", 2)
    transcript = proj / "s1.jsonl"
    transcript.write_text("".join(record(f"m{i}") for i in range(5)))

    usage_mod.report_session("s1", str(transcript))
    assert [len(b["messages"]) for b in fake.batches] == [2, 2, 1]
    assert all(b["session_id"] == "s1" for b in fake.batches)


def test_backfill_walks_every_transcript(env, tmp_path):
    fake, proj = env
    (proj / "s1.jsonl").write_text(record("m1"))
    (proj / "s2.jsonl").write_text(record("m2"))
    other = tmp_path / "projects" / "-home-giosue-projects-pquant"
    other.mkdir(parents=True)
    (other / "s3.jsonl").write_text(record("m3"))

    assert usage_mod.run_backfill(str(tmp_path / "projects")) == 0
    assert sorted(fake.sent_ids) == ["m1", "m2", "m3"]
    assert {b["session_id"] for b in fake.batches} == {"s1", "s2", "s3"}

    # Re-running is a near no-op: offsets were recorded per session.
    fake.batches.clear()
    assert usage_mod.run_backfill(str(tmp_path / "projects")) == 0
    assert fake.batches == []
