"""Usage ingestion + summary endpoints.

The load-bearing property is idempotence: message_id is the primary key and
ingest is INSERT OR IGNORE, which is what makes Stop-hook retries, backfill
re-runs, and resumed sessions (which copy prior history under a new session_id)
safe. Most of these tests exist to pin that down.
"""

import pytest

pytestmark = pytest.mark.asyncio


def _msg(message_id: str, **over):
    base = {
        "message_id": message_id,
        "ts": "2026-08-09T14:44:02.651Z",
        "model": "claude-opus-5",
        "cwd": "/home/giosue/projects/hydra",
        "effort": "xhigh",
        "input_tokens": 100,
        "output_tokens": 1000,
        "cache_read_tokens": 20000,
        "cache_write_5m_tokens": 0,
        "cache_write_1h_tokens": 4000,
    }
    base.update(over)
    return base


async def _post(client, session_id, messages, instance="pi"):
    return await client.post(
        "/api/usage/messages",
        json={"session_id": session_id, "messages": messages},
        headers={"X-Instance-Id": instance},
    )


async def test_ingest_and_summary(client):
    res = await _post(client, "s1", [_msg("m1"), _msg("m2")])
    assert res.status_code == 200
    assert res.json() == {"inserted": 2, "ignored": 0}

    res = await client.get("/api/usage/summary?group_by=day")
    body = res.json()
    assert [r["key"] for r in body["rows"]] == ["2026-08-09"]
    assert body["totals"]["messages"] == 2
    assert body["totals"]["output_tokens"] == 2000
    assert body["unpriced_models"] == []


async def test_replaying_a_batch_inserts_nothing(client):
    await _post(client, "s1", [_msg("m1"), _msg("m2")])
    res = await _post(client, "s1", [_msg("m1"), _msg("m2")])
    assert res.json() == {"inserted": 0, "ignored": 2}

    body = (await client.get("/api/usage/summary?group_by=day")).json()
    assert body["totals"]["messages"] == 2


async def test_same_message_under_a_second_session_is_ignored(client):
    """A resume or fork copies prior history into a new transcript file, so the
    same message.id legitimately arrives again under a different session_id.
    It must not be counted twice."""
    await _post(client, "s1", [_msg("m1")])
    res = await _post(client, "s2-resumed", [_msg("m1"), _msg("m2")])
    assert res.json() == {"inserted": 1, "ignored": 1}

    body = (await client.get("/api/usage/summary?group_by=day")).json()
    assert body["totals"]["messages"] == 2


async def test_row_inserts_without_a_matching_session(client):
    """usage_messages.session_id carries no FK on purpose: backfill imports
    transcripts for sessions the server has never seen."""
    res = await _post(client, "never-reported", [_msg("m1")])
    assert res.json()["inserted"] == 1


async def test_empty_batch(client):
    res = await _post(client, "s1", [])
    assert res.json() == {"inserted": 0, "ignored": 0}


async def test_unknown_model_is_unpriced_not_free(client):
    await _post(client, "s1", [_msg("m1", model="claude-from-the-future-9")])
    body = (await client.get("/api/usage/summary?group_by=model")).json()

    assert body["unpriced_models"] == ["claude-from-the-future-9"]
    assert body["totals"]["unpriced_messages"] == 1
    assert body["totals"]["cost_usd"] == 0.0
    # The tokens are still counted - only the money is unknown.
    assert body["totals"]["output_tokens"] == 1000


async def test_cost_is_priced_per_model_then_summed(client):
    await _post(client, "s1", [
        _msg("m1", model="claude-opus-5", input_tokens=1_000_000,
             output_tokens=0, cache_read_tokens=0, cache_write_1h_tokens=0),
        _msg("m2", model="claude-haiku-4-5-20251001", input_tokens=1_000_000,
             output_tokens=0, cache_read_tokens=0, cache_write_1h_tokens=0),
    ])
    body = (await client.get("/api/usage/summary?group_by=day")).json()
    # $5/MTok for opus-5 + $1/MTok for the dated haiku id = $6
    assert body["totals"]["cost_usd"] == pytest.approx(6.0)


async def test_cost_components_split_by_token_kind(client):
    """Token volume and cost have different shapes - cache reads dominate one and
    not the other - so the split is computed server-side from the same rates."""
    await _post(client, "s1", [_msg(
        "m1", model="claude-opus-5",
        input_tokens=0, output_tokens=1_000_000,
        cache_read_tokens=10_000_000,        # 10M * $5 * 0.1 = $5
        cache_write_5m_tokens=0, cache_write_1h_tokens=1_000_000,  # 1M * $5 * 2 = $10
    )])
    body = (await client.get("/api/usage/summary?group_by=day")).json()
    parts = body["totals"]["cost_components"]

    assert parts["output"] == pytest.approx(25.0)      # 1M * $25
    assert parts["cache_read"] == pytest.approx(5.0)
    assert parts["cache_write_1h"] == pytest.approx(10.0)
    assert parts["cache_write_5m"] == 0.0
    assert sum(parts.values()) == pytest.approx(body["totals"]["cost_usd"])


async def test_unpriced_model_contributes_no_components(client):
    await _post(client, "s1", [_msg("m1", model="claude-from-the-future-9")])
    body = (await client.get("/api/usage/summary?group_by=day")).json()
    assert body["totals"]["cost_components"] == {
        "input": 0.0, "output": 0.0, "cache_read": 0.0,
        "cache_write_5m": 0.0, "cache_write_1h": 0.0, "web_search": 0.0,
    }
    assert body["totals"]["unpriced_messages"] == 1


async def test_group_by_project_resolves_registered_paths(client):
    await client.post(
        "/api/projects",
        json={"slug": "hydra", "path": "/home/giosue/projects/hydra"},
        headers={"X-Instance-Id": "pi"},
    )
    await _post(client, "s1", [
        _msg("m1", cwd="/home/giosue/projects/hydra"),
        _msg("m2", cwd="/somewhere/unregistered"),
    ])

    body = (await client.get("/api/usage/summary?group_by=project")).json()
    keys = {r["key"] for r in body["rows"]}
    assert keys == {"hydra", "unregistered"}


async def test_project_registered_on_two_machines_does_not_double_count(client):
    """project_paths is keyed (slug, instance_id), so two machines sharing an
    absolute path would fan the join out if it weren't de-duplicated first."""
    for instance in ("pi", "laptop"):
        await client.post(
            "/api/projects",
            json={"slug": "hydra", "path": "/home/giosue/projects/hydra"},
            headers={"X-Instance-Id": instance},
        )
    await _post(client, "s1", [_msg("m1", cwd="/home/giosue/projects/hydra")])

    body = (await client.get("/api/usage/summary?group_by=project")).json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["messages"] == 1


async def test_project_group_uses_confirmed_ancestor(client):
    await client.post(
        "/api/projects",
        json={"slug": "hydra", "path": "/work/demo"},
        headers={"X-Instance-Id": "pi"},
    )
    await _post(client, "s1", [_msg("m1", cwd="/work/demo/server")])

    body = (await client.get("/api/usage/summary?group_by=project")).json()
    assert [(row["key"], row["messages"]) for row in body["rows"]] == [("hydra", 1)]


async def test_project_group_uses_longest_confirmed_ancestor(client):
    for slug, path in (
        ("outer", "/work/repo"),
        ("inner", "/work/repo/packages/app"),
    ):
        await client.post(
            "/api/projects",
            json={"slug": slug, "path": path},
            headers={"X-Instance-Id": slug},
        )
    await _post(client, "s1", [_msg("m1", cwd="/work/repo/packages/app/src")])

    body = (await client.get("/api/usage/summary?group_by=project")).json()
    assert [row["key"] for row in body["rows"]] == ["inner"]


async def test_project_group_exact_match_beats_confirmed_ancestor(client):
    auto = await client.post(
        "/api/projects/auto-register",
        json={"cwd": "/work/repo/sub"},
        headers={"X-Instance-Id": "auto"},
    )
    assert auto.json()["status"] == "created"
    await client.post(
        "/api/projects",
        json={"slug": "repo", "path": "/work/repo"},
        headers={"X-Instance-Id": "manual"},
    )
    await _post(client, "s1", [_msg("m1", cwd="/work/repo/sub")])

    body = (await client.get("/api/usage/summary?group_by=project")).json()
    assert [row["key"] for row in body["rows"]] == ["sub"]


async def test_unconfirmed_path_does_not_capture_usage(client):
    auto = await client.post(
        "/api/projects/auto-register",
        json={"cwd": "/work/junk"},
        headers={"X-Instance-Id": "auto"},
    )
    assert auto.json()["status"] == "created"
    await client.post(
        "/api/projects",
        json={"slug": "work", "path": "/work"},
        headers={"X-Instance-Id": "manual"},
    )
    await _post(client, "s1", [_msg("m1", cwd="/work/junk/src")])

    body = (await client.get("/api/usage/summary?group_by=project")).json()
    assert [row["key"] for row in body["rows"]] == ["work"]


async def test_project_prefix_does_not_treat_underscore_as_wildcard(client):
    await client.post(
        "/api/projects",
        json={"slug": "proj_a", "path": "/home/u/proj_a"},
        headers={"X-Instance-Id": "pi"},
    )
    await _post(client, "s1", [_msg("m1", cwd="/home/u/projXa/src")])

    body = (await client.get("/api/usage/summary?group_by=project")).json()
    assert [row["key"] for row in body["rows"]] == ["unregistered"]


async def test_unrelated_usage_stays_unregistered(client):
    await client.post(
        "/api/projects",
        json={"slug": "hydra", "path": "/work/hydra"},
        headers={"X-Instance-Id": "pi"},
    )
    await _post(client, "s1", [_msg("m1", cwd="/elsewhere/repo")])

    body = (await client.get("/api/usage/summary?group_by=project")).json()
    assert [row["key"] for row in body["rows"]] == ["unregistered"]


async def test_duplicate_prefixes_do_not_fan_out_usage(client):
    for slug, instance in (("alpha", "a"), ("beta", "b")):
        await client.post(
            "/api/projects",
            json={"slug": slug, "path": "/work/repo"},
            headers={"X-Instance-Id": instance},
        )
    await _post(client, "s1", [_msg("m1", cwd="/work/repo/src")])

    body = (await client.get("/api/usage/summary?group_by=project")).json()
    assert [(row["key"], row["messages"]) for row in body["rows"]] == [("alpha", 1)]
    assert body["totals"]["messages"] == 1


async def test_group_by_agent_and_instance(client):
    await _post(client, "s1", [_msg("m1")], instance="pi")
    await _post(client, "s2", [
        _msg("m2", is_subagent=True, agent_type="Explore"),
    ], instance="laptop")

    agents = (await client.get("/api/usage/summary?group_by=agent")).json()
    assert {r["key"] for r in agents["rows"]} == {"main", "Explore"}

    machines = (await client.get("/api/usage/summary?group_by=instance")).json()
    assert {r["key"] for r in machines["rows"]} == {"pi", "laptop"}


async def test_harness_defaults_groups_and_filters(client):
    await _post(client, "s1", [_msg("m1")], instance="pi")
    await _post(
        client,
        "s2",
        [_msg("m2", harness="codex-cli", model="gpt-5.6-sol")],
        instance="laptop",
    )

    harnesses = (await client.get("/api/usage/summary?group_by=harness")).json()
    assert {r["key"] for r in harnesses["rows"]} == {"claude-code", "codex-cli"}

    codex = (
        await client.get(
            "/api/usage/summary?group_by=instance&harness=codex-cli"
        )
    ).json()
    assert [(row["key"], row["messages"]) for row in codex["rows"]] == [
        ("laptop", 1)
    ]
    assert codex["harness"] == "codex-cli"

    claude = (
        await client.get(
            "/api/usage/summary?group_by=harness&instance=pi"
        )
    ).json()
    assert [(row["key"], row["messages"]) for row in claude["rows"]] == [
        ("claude-code", 1)
    ]


async def test_instance_filter(client):
    await _post(client, "s1", [_msg("m1"), _msg("m2")], instance="pi")
    await _post(client, "s2", [_msg("m3")], instance="laptop")

    both = (await client.get("/api/usage/summary?group_by=day")).json()
    assert both["totals"]["messages"] == 3
    assert both["instance"] is None

    pi = (await client.get("/api/usage/summary?group_by=day&instance=pi")).json()
    assert pi["totals"]["messages"] == 2
    assert pi["instance"] == "pi"

    # The filter composes with the time window rather than replacing it.
    scoped = await client.get(
        "/api/usage/summary?group_by=day&instance=laptop&since=2026-08-09"
    )
    assert scoped.json()["totals"]["messages"] == 1

    missing = (await client.get("/api/usage/summary?group_by=day&instance=nope")).json()
    assert missing["totals"]["messages"] == 0
    assert missing["rows"] == []


async def test_since_and_until_filter(client):
    await _post(client, "s1", [
        _msg("old", ts="2026-07-01T10:00:00.000Z"),
        _msg("new", ts="2026-08-09T10:00:00.000Z"),
    ])

    body = (await client.get("/api/usage/summary?group_by=day&since=2026-08-01")).json()
    assert body["totals"]["messages"] == 1
    assert [r["key"] for r in body["rows"]] == ["2026-08-09"]

    body = (await client.get("/api/usage/summary?group_by=day&until=2026-08-01")).json()
    assert body["totals"]["messages"] == 1
    assert [r["key"] for r in body["rows"]] == ["2026-07-01"]


async def test_days_sort_newest_first(client):
    await _post(client, "s1", [
        _msg("a", ts="2026-08-01T10:00:00.000Z"),
        _msg("b", ts="2026-08-09T10:00:00.000Z"),
        _msg("c", ts="2026-08-05T10:00:00.000Z"),
    ])
    body = (await client.get("/api/usage/summary?group_by=day")).json()
    assert [r["key"] for r in body["rows"]] == ["2026-08-09", "2026-08-05", "2026-08-01"]
