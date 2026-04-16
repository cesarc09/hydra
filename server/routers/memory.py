from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from server.auth import require_auth
from server.db import get_db
from server.models import MemoryCreate, MemoryItem, MemoryUpdate

router = APIRouter(
    prefix="/api/memory", tags=["memory"], dependencies=[Depends(require_auth)]
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@router.get("")
async def list_memories() -> list[MemoryItem]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM memories ORDER BY id")
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


@router.post("", status_code=201)
async def create_memory(memory: MemoryCreate) -> MemoryItem:
    db = await get_db()
    now = _now()
    cursor = await db.execute(
        "INSERT INTO memories (name, description, type, body, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (memory.name, memory.description, memory.type, memory.body, now, now),
    )
    await db.commit()
    return MemoryItem(
        id=cursor.lastrowid,  # type: ignore[arg-type]
        name=memory.name,
        description=memory.description,
        type=memory.type,
        body=memory.body,
        created_at=now,
        updated_at=now,
    )


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
