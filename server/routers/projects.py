from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from server.auth import require_auth
from server.db import get_db
from server.models import ProjectCreate, ProjectItem, ProjectUpdate

router = APIRouter(
    prefix="/api/projects", tags=["projects"], dependencies=[Depends(require_auth)]
)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@router.get("")
async def list_projects() -> list[ProjectItem]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM projects ORDER BY slug")
    return [ProjectItem(**dict(r)) for r in rows]


@router.get("/{slug}")
async def get_project(slug: str) -> ProjectItem:
    db = await get_db()
    rows = list(await db.execute_fetchall(
        "SELECT * FROM projects WHERE slug = ?", (slug,)
    ))
    if not rows:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectItem(**dict(rows[0]))


@router.post("", status_code=201)
async def create_project(project: ProjectCreate) -> ProjectItem:
    db = await get_db()
    now = _now()
    try:
        await db.execute(
            "INSERT INTO projects (slug, path, description, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (project.slug, project.path, project.description, now, now),
        )
        await db.commit()
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(
                status_code=409, detail="Project slug already exists"
            ) from None
        raise
    return ProjectItem(
        slug=project.slug,
        path=project.path,
        description=project.description,
        created_at=now,
        updated_at=now,
    )


@router.put("/{slug}")
async def update_project(slug: str, update: ProjectUpdate) -> ProjectItem:
    db = await get_db()
    rows = list(await db.execute_fetchall(
        "SELECT * FROM projects WHERE slug = ?", (slug,)
    ))
    if not rows:
        raise HTTPException(status_code=404, detail="Project not found")

    current = dict(rows[0])
    now = _now()
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    fields["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = [*fields.values(), slug]
    await db.execute(f"UPDATE projects SET {set_clause} WHERE slug = ?", values)
    await db.commit()

    current.update(fields)
    return ProjectItem(**current)


@router.delete("/{slug}", status_code=204)
async def delete_project(slug: str):
    db = await get_db()
    cursor = await db.execute("DELETE FROM projects WHERE slug = ?", (slug,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    await db.commit()
