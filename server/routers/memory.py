import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from server.auth import require_auth
from server.db import get_db
from server.models import MemoryCreate, MemoryItem, MemoryUpdate

router = APIRouter(
    prefix="/api/memory", tags=["memory"], dependencies=[Depends(require_auth)]
)


def _now() -> str:
    """Timestamp for created_at/updated_at.

    Keeps microseconds, unlike the rest of the API. `updated_at` is the version
    token `hydra sync` uses to decide whether a memory changed on the server
    since a mirror file was written - so two writes to one row MUST produce two
    different values. Truncated to whole seconds, a re-scope landing in the same
    second as the mirror's recorded version is invisible, and the stale mirror
    silently reverts it on the next push.
    """
    return datetime.now(UTC).isoformat()


GLOBAL_TYPES = frozenset({"user", "feedback"})
PROJECT_TYPES = frozenset({"project", "reference"})


def _type_for_scope(mem_type: str, project_slug: str | None) -> str:
    """Keep a memory's type consistent with its scope, in BOTH directions.

    Scope is derived from type everywhere (CLI create, dashboard moves), so a
    row whose type and scope disagree has no stable reading. Hence:

    - Pinned (project_slug set) + a global type -> coerced to 'project'. This is
      what auto-scopes the dashboard's Move-to-project.
    - Global (project_slug NULL) + a project-scoped type -> rejected (422). We
      cannot coerce this direction, because there is no way to guess user vs
      feedback - the caller has to say. Silently leaving it would produce a
      global row that sync re-pins to whatever project the next session runs in.
    """
    if project_slug is not None and mem_type in GLOBAL_TYPES:
        return "project"
    if project_slug is None and mem_type in PROJECT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"A global memory cannot have type '{mem_type}'; pass a global"
                " type (user or feedback), or pin it to a project."
            ),
        )
    return mem_type


@router.get("")
async def list_memories(
    project_slug: str | None = None,
    include_global: bool = False,
) -> list[MemoryItem]:
    """List memories. Unfiltered returns everything.

    With project_slug: returns memories pinned to that project; optionally
    also global (project_slug IS NULL) memories when include_global=true.
    """
    db = await get_db()
    if project_slug is None:
        rows = await db.execute_fetchall("SELECT * FROM memories ORDER BY id")
    elif include_global:
        rows = await db.execute_fetchall(
            "SELECT * FROM memories WHERE project_slug = ? OR project_slug IS NULL"
            " ORDER BY id",
            (project_slug,),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM memories WHERE project_slug = ? ORDER BY id",
            (project_slug,),
        )
    return [MemoryItem(**dict(r)) for r in rows]


@router.get("/{memory_id}")
async def get_memory(memory_id: int) -> MemoryItem:
    db = await get_db()
    rows = list(await db.execute_fetchall(
        "SELECT * FROM memories WHERE id = ?", (memory_id,)
    ))
    if not rows:
        raise HTTPException(status_code=404, detail="Memory not found")
    return MemoryItem(**dict(rows[0]))


@router.post("")
async def upsert_memory(memory: MemoryCreate) -> MemoryItem:
    """Upsert on name. Names are globally unique: one name = one memory,
    whatever its scope.

    A POST that would move an existing memory to a different scope is refused
    with 409 unless `rescope` is set: a by-name upsert must never be able to
    silently unpin a memory someone deliberately scoped to a project.
    """
    db = await get_db()
    now = _now()
    mem_type = _type_for_scope(memory.type, memory.project_slug)

    rows = list(await db.execute_fetchall(
        "SELECT id, project_slug FROM memories WHERE name = ?", (memory.name,)
    ))
    if rows and rows[0]["project_slug"] != memory.project_slug and not memory.rescope:
        held_by = rows[0]["project_slug"] or "global"
        raise HTTPException(
            status_code=409,
            detail=(
                f"Memory '{memory.name}' already exists in scope '{held_by}';"
                " memory names are globally unique. Rename it, or pass"
                " rescope=true to move the existing memory to this scope."
            ),
        )

    sql = (
        "INSERT INTO memories (name, description, type, body, project_slug,"
        " author_harness, author_session_id, author_model, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(name) DO UPDATE SET description=excluded.description,"
        " type=excluded.type, body=excluded.body,"
        " project_slug=excluded.project_slug,"
        " author_harness=excluded.author_harness,"
        " author_session_id=excluded.author_session_id,"
        " author_model=excluded.author_model, updated_at=excluded.updated_at"
        " RETURNING *"
    )
    params = (
        memory.name, memory.description, mem_type, memory.body,
        memory.project_slug, memory.author_harness, memory.author_session_id,
        memory.author_model, now, now,
    )
    try:
        result = list(await db.execute_fetchall(sql, params))
    except sqlite3.IntegrityError as e:
        raise HTTPException(
            status_code=400, detail=f"Cannot save memory: {e}"
        ) from e
    await db.commit()
    return MemoryItem(**dict(result[0]))


@router.put("/{memory_id}")
async def update_memory(memory_id: int, update: MemoryUpdate) -> MemoryItem:
    db = await get_db()
    rows = list(await db.execute_fetchall(
        "SELECT * FROM memories WHERE id = ?", (memory_id,)
    ))
    if not rows:
        raise HTTPException(status_code=404, detail="Memory not found")

    current = dict(rows[0])
    now = _now()
    # exclude_unset, not "drop the Nones": an explicit {"project_slug": null}
    # must be able to unpin a memory to global scope, which is how a re-scope
    # travels without deleting and re-creating the row (and minting a new id).
    author_keys = {"author_harness", "author_session_id", "author_model"}
    fields = update.model_dump(exclude_unset=True, exclude=author_keys)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    for key, value in fields.items():
        if value is None and key != "project_slug":
            raise HTTPException(status_code=422, detail=f"'{key}' cannot be null")

    # Keep type consistent with scope when either is changing (e.g. pinning a
    # global memory to a project via PUT without sending a new type).
    eff_slug = fields.get("project_slug", current["project_slug"])
    eff_type = fields.get("type", current["type"])
    coerced = _type_for_scope(eff_type, eff_slug)
    if coerced != eff_type:
        fields["type"] = coerced

    fields.update({key: getattr(update, key) for key in author_keys})
    fields["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = [*fields.values(), memory_id]
    try:
        await db.execute(f"UPDATE memories SET {set_clause} WHERE id = ?", values)
    except sqlite3.IntegrityError as e:
        if "UNIQUE" in str(e):
            raise HTTPException(
                status_code=409,
                detail=f"Another memory is already named '{fields.get('name')}'",
            ) from e
        raise HTTPException(status_code=400, detail=f"Cannot update memory: {e}") from e
    await db.commit()

    updated = list(await db.execute_fetchall(
        "SELECT * FROM memories WHERE id = ?", (memory_id,)
    ))
    return MemoryItem(**dict(updated[0]))


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(memory_id: int):
    db = await get_db()
    cursor = await db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Memory not found")
    await db.commit()
