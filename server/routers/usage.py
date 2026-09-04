"""Token usage ingestion and reporting.

Ingest is idempotent by construction: `usage_messages.message_id` is the primary
key and every insert is `INSERT OR IGNORE`. That is what makes the client's
Stop-hook reporting safe to retry, makes `usage backfill` re-runnable, and stops
a resumed session (which copies prior history into a new transcript file, under
a new session_id) from counting the same API message twice.

Cost is never stored. `server/pricing.py` prices grouped rows on the way out, so
a rate correction retroactively fixes every figure.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query

from server import pricing
from server.auth import require_auth
from server.db import get_db
from server.models import UsageBatch

router = APIRouter(
    prefix="/api/usage", tags=["usage"], dependencies=[Depends(require_auth)]
)

GroupBy = Literal["day", "model", "project", "instance", "harness", "agent"]

# The counter columns, in one place: summed in SQL, echoed in the response, and
# fed to the pricer. Adding a counter means touching only this tuple + schema.
_COUNTERS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
    "web_search_requests",
    "web_fetch_requests",
)

# Group-key SQL per `group_by`. Project resolution uses scalar subqueries so
# duplicate matching paths cannot fan out usage rows.
_PROJECT_SQL = (
    "COALESCE("
    " (SELECT pe.slug FROM project_paths pe"
    "  WHERE pe.path = u.cwd ORDER BY pe.slug LIMIT 1),"
    " (SELECT pp.slug FROM project_paths pp"
    "  JOIN projects pr ON pr.slug = pp.slug"
    "  WHERE pr.auto_registered_at IS NULL"
    "   AND substr(u.cwd, 1, length(pp.path) + 1)"
    "       IN (pp.path || '/', pp.path || '\\')"
    "  ORDER BY length(pp.path) DESC, pp.slug LIMIT 1),"
    " 'unregistered')"
)

_GROUP_SQL: dict[str, str] = {
    "day": "substr(u.ts, 1, 10)",
    "model": "u.model",
    "instance": "u.instance_id",
    "harness": "u.harness",
    "agent": (
        "CASE WHEN u.is_subagent = 0 THEN 'main'"
        " ELSE COALESCE(u.agent_type, 'subagent') END"
    ),
    "project": _PROJECT_SQL,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


@router.post("/messages")
async def ingest_usage(
    batch: UsageBatch,
    x_instance_id: str = Header(default="unknown"),
):
    """Store a batch of per-message usage rows. Already-known message ids are
    ignored, so the caller can resend freely."""
    if not batch.messages:
        return {"inserted": 0, "ignored": 0}

    db = await get_db()
    ids = list({m.message_id for m in batch.messages})
    placeholders = ",".join("?" * len(ids))
    known = {
        row["message_id"]
        for row in await db.execute_fetchall(
            f"SELECT message_id FROM usage_messages WHERE message_id IN ({placeholders})",
            ids,
        )
    }

    received_at = _now()
    await db.executemany(
        "INSERT OR IGNORE INTO usage_messages ("
        " message_id, session_id, instance_id, harness, ts, cwd, model, effort,"
        " is_subagent, agent_type, service_tier, speed,"
        " input_tokens, output_tokens, cache_read_tokens,"
        " cache_write_5m_tokens, cache_write_1h_tokens,"
        " web_search_requests, web_fetch_requests, received_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                m.message_id, batch.session_id, x_instance_id, m.harness, m.ts, m.cwd,
                m.model, m.effort, int(m.is_subagent), m.agent_type,
                m.service_tier, m.speed,
                m.input_tokens, m.output_tokens, m.cache_read_tokens,
                m.cache_write_5m_tokens, m.cache_write_1h_tokens,
                m.web_search_requests, m.web_fetch_requests, received_at,
            )
            for m in batch.messages
        ],
    )
    await db.commit()

    inserted = len(ids) - len(known)
    return {"inserted": inserted, "ignored": len(batch.messages) - inserted}


_COST_PARTS = (
    "input", "output", "cache_read", "cache_write_5m", "cache_write_1h", "web_search",
)


def _blank_row(key: str) -> dict[str, Any]:
    row: dict[str, Any] = {"key": key, "messages": 0}
    row.update({c: 0 for c in _COUNTERS})
    row["cost_usd"] = 0.0
    row["cost_components"] = {p: 0.0 for p in _COST_PARTS}
    row["unpriced_messages"] = 0
    return row


def _fold(
    target: dict[str, Any], src: dict[str, Any], parts: dict[str, float] | None
) -> None:
    """Accumulate one (key, model) bucket into a group row.

    Cost has to be summed per model, because the rate table is per model - a
    group that mixes models cannot be priced from its summed counters.
    """
    target["messages"] += src["messages"]
    for c in _COUNTERS:
        target[c] += src[c]
    if parts is None:
        target["unpriced_messages"] += src["messages"]
        return
    target["cost_usd"] += sum(parts.values())
    for name, value in parts.items():
        target["cost_components"][name] += value


@router.get("/summary")
async def usage_summary(
    group_by: GroupBy = "day",
    since: str | None = Query(default=None, description="ISO date/datetime, inclusive"),
    until: str | None = Query(default=None, description="ISO date/datetime, exclusive"),
    instance: str | None = Query(default=None, description="restrict to one machine"),
    harness: str | None = Query(default=None, description="restrict to one harness"),
):
    """Grouped token totals plus reconstructed cost.

    Rows are aggregated in SQL per (group key, model) and folded in Python, so
    each model's counters are priced at its own rate before being summed into
    the group. Messages on a model the rate table doesn't know contribute their
    tokens but no cost, and are counted in `unpriced_messages`.
    """
    db = await get_db()
    key_sql = _GROUP_SQL[group_by]

    where = []
    params: list[Any] = []
    if since:
        where.append("u.ts >= ?")
        params.append(since)
    if until:
        where.append("u.ts < ?")
        params.append(until)
    if instance:
        where.append("u.instance_id = ?")
        params.append(instance)
    if harness:
        where.append("u.harness = ?")
        params.append(harness)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    sums = ", ".join(f"SUM(u.{c}) AS {c}" for c in _COUNTERS)
    rows = await db.execute_fetchall(
        f"SELECT {key_sql} AS key, u.model AS model, COUNT(*) AS messages, {sums}"
        f" FROM usage_messages u{where_sql}"
        " GROUP BY key, u.model",
        params,
    )

    grouped: dict[str, dict[str, Any]] = {}
    totals = _blank_row("total")
    unpriced_models: set[str] = set()
    for row in rows:
        bucket = dict(row)
        parts = pricing.cost_components(
            bucket["model"],
            input_tokens=bucket["input_tokens"],
            output_tokens=bucket["output_tokens"],
            cache_read_tokens=bucket["cache_read_tokens"],
            cache_write_5m_tokens=bucket["cache_write_5m_tokens"],
            cache_write_1h_tokens=bucket["cache_write_1h_tokens"],
            web_search_requests=bucket["web_search_requests"],
        )
        if parts is None:
            unpriced_models.add(bucket["model"])
        target = grouped.setdefault(str(bucket["key"]), _blank_row(str(bucket["key"])))
        _fold(target, bucket, parts)
        _fold(totals, bucket, parts)

    out = list(grouped.values())
    if group_by == "day":
        out.sort(key=lambda r: r["key"], reverse=True)
    else:
        out.sort(key=lambda r: r["cost_usd"] or 0, reverse=True)

    return {
        "group_by": group_by,
        "since": since,
        "until": until,
        "instance": instance,
        "harness": harness,
        "rows": out,
        "totals": totals,
        "unpriced_models": sorted(unpriced_models),
    }
