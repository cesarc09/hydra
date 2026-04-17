from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException

from server.auth import require_auth
from server.db import get_db
from server.models import ProjectCreate, ProjectItem, ProjectPath, ProjectUpdate

router = APIRouter(
    prefix="/api/projects", tags=["projects"], dependencies=[Depends(require_auth)]
)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


async def _fetch_paths(db, slug: str) -> list[ProjectPath]:
    rows = await db.execute_fetchall(
        "SELECT instance_id, path FROM project_paths WHERE slug = ? ORDER BY instance_id",
        (slug,),
    )
    return [ProjectPath(instance_id=r["instance_id"], path=r["path"]) for r in rows]


async def _build_item(db, row) -> ProjectItem:
    return ProjectItem(
        slug=row["slug"],
        description=row["description"],
        paths=await _fetch_paths(db, row["slug"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("")
async def list_projects() -> list[ProjectItem]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM projects ORDER BY slug")
    return [await _build_item(db, r) for r in rows]


@router.get("/{slug}")
async def get_project(slug: str) -> ProjectItem:
    db = await get_db()
    rows = list(await db.execute_fetchall(
        "SELECT * FROM projects WHERE slug = ?", (slug,)
    ))
    if not rows:
        raise HTTPException(status_code=404, detail="Project not found")
    return await _build_item(db, rows[0])


@router.post("", status_code=201)
async def create_project(
    project: ProjectCreate,
    x_instance_id: str = Header(default="unknown"),
) -> ProjectItem:
    """Idempotent on slug. If the slug is new, creates it. If the slug exists,
    upserts the (slug, instance_id) path — same machine re-registers update the
    row; a new machine adds a new row. Description is only written on create."""
    db = await get_db()
    now = _now()

    rows = list(await db.execute_fetchall(
        "SELECT * FROM projects WHERE slug = ?", (project.slug,)
    ))
    if not rows:
        await db.execute(
            "INSERT INTO projects (slug, description, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (project.slug, project.description, now, now),
        )

    await db.execute(
        "INSERT INTO project_paths (slug, instance_id, path, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(slug, instance_id) DO UPDATE SET"
        "   path = excluded.path, updated_at = excluded.updated_at",
        (project.slug, x_instance_id, project.path, now, now),
    )
    await db.execute(
        "UPDATE projects SET updated_at = ? WHERE slug = ?", (now, project.slug)
    )
    await db.commit()

    rows = list(await db.execute_fetchall(
        "SELECT * FROM projects WHERE slug = ?", (project.slug,)
    ))
    return await _build_item(db, rows[0])


@router.put("/{slug}")
async def update_project(slug: str, update: ProjectUpdate) -> ProjectItem:
    db = await get_db()
    rows = list(await db.execute_fetchall(
        "SELECT * FROM projects WHERE slug = ?", (slug,)
    ))
    if not rows:
        raise HTTPException(status_code=404, detail="Project not found")

    now = _now()
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    fields["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = [*fields.values(), slug]
    await db.execute(f"UPDATE projects SET {set_clause} WHERE slug = ?", values)
    await db.commit()

    rows = list(await db.execute_fetchall(
        "SELECT * FROM projects WHERE slug = ?", (slug,)
    ))
    return await _build_item(db, rows[0])


@router.delete("/{slug}", status_code=204)
async def delete_project(slug: str):
    db = await get_db()
    cursor = await db.execute("DELETE FROM projects WHERE slug = ?", (slug,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    await db.commit()


@router.delete("/{slug}/paths/{instance_id}", status_code=204)
async def delete_project_path(slug: str, instance_id: str):
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM project_paths WHERE slug = ? AND instance_id = ?",
        (slug, instance_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Path not registered")
    await db.commit()
