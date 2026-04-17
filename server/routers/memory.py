from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from server.auth import require_auth
from server.db import get_db
from server.models import MemoryCreate, MemoryItem, MemoryUpdate

router = APIRouter(
    prefix="/api/memory", tags=["memory"], dependencies=[Depends(require_auth)]
)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


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
    """Upsert on (name, project_slug). Global memories (project_slug IS NULL)
    are unique by name; project-pinned memories are unique within their project.
    """
    db = await get_db()
    now = _now()
    # SQLite's ON CONFLICT requires naming the conflict target. Partial unique
    # indexes are valid targets; we use WHERE to pick the right one at runtime.
    if memory.project_slug is None:
        sql = (
            "INSERT INTO memories (name, description, type, body, project_slug,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, NULL, ?, ?)"
            " ON CONFLICT(name) WHERE project_slug IS NULL DO UPDATE SET"
            " description=excluded.description, type=excluded.type,"
            " body=excluded.body, updated_at=excluded.updated_at"
            " RETURNING *"
        )
        params = (memory.name, memory.description, memory.type, memory.body, now, now)
    else:
        sql = (
            "INSERT INTO memories (name, description, type, body, project_slug,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(name, project_slug) WHERE project_slug IS NOT NULL"
            " DO UPDATE SET description=excluded.description, type=excluded.type,"
            " body=excluded.body, updated_at=excluded.updated_at"
            " RETURNING *"
        )
        params = (
            memory.name, memory.description, memory.type, memory.body,
            memory.project_slug, now, now,
        )
    rows = list(await db.execute_fetchall(sql, params))
    await db.commit()
    return MemoryItem(**dict(rows[0]))


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
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    fields["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = [*fields.values(), memory_id]
    await db.execute(f"UPDATE memories SET {set_clause} WHERE id = ?", values)
    await db.commit()

    current.update(fields)
    return MemoryItem(**current)


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(memory_id: int):
    db = await get_db()
    cursor = await db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Memory not found")
    await db.commit()
