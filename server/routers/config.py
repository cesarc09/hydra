from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from server.auth import require_auth
from server.db import get_db

router = APIRouter(prefix="/api/config", tags=["config"], dependencies=[Depends(require_auth)])


@router.get("/claude-md")
async def get_claude_md():
    db = await get_db()
    rows = list(await db.execute_fetchall("SELECT content FROM claude_md WHERE id = 1"))
    content = rows[0][0] if rows else ""
    return Response(content=content, media_type="text/plain")


@router.put("/claude-md")
async def put_claude_md(request: Request):
    content = (await request.body()).decode("utf-8")
    if not content.strip():
        raise HTTPException(status_code=400, detail="CLAUDE.md content cannot be empty")
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    db = await get_db()
    await db.execute(
        """INSERT INTO claude_md (id, content, updated_at) VALUES (1, ?, ?)
           ON CONFLICT(id) DO UPDATE SET content = ?, updated_at = ?""",
        (content, now, content, now),
    )
    await db.commit()
    return {"status": "ok", "updated_at": now}
