import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from server.auth import require_auth
from server.db import get_db

router = APIRouter(prefix="/api/config", tags=["config"], dependencies=[Depends(require_auth)])

# A command name maps 1:1 to a filename (<name>.md -> /<name>) on the client, so
# restrict it to a filesystem- and command-safe charset: no path separators, no
# leading dot, nothing that could escape ~/.claude/commands or rename a command.
_COMMAND_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


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


@router.get("/commands")
async def list_commands() -> dict[str, str]:
    """Return every distributed command as a {name: content} map. The client
    pulls this in one round trip, writes each file, and prunes the rest."""
    db = await get_db()
    rows = await db.execute_fetchall("SELECT name, content FROM config_commands")
    return {row[0]: row[1] for row in rows}


@router.get("/commands/{name}")
async def get_command(name: str):
    db = await get_db()
    rows = list(
        await db.execute_fetchall(
            "SELECT content FROM config_commands WHERE name = ?", (name,)
        )
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Command not found")
    return Response(content=rows[0][0], media_type="text/plain")


@router.put("/commands/{name}")
async def put_command(name: str, request: Request):
    if not _COMMAND_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid command name")
    content = (await request.body()).decode("utf-8")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Command content cannot be empty")
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    db = await get_db()
    await db.execute(
        """INSERT INTO config_commands (name, content, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET content = ?, updated_at = ?""",
        (name, content, now, content, now),
    )
    await db.commit()
    return {"status": "ok", "updated_at": now}


@router.delete("/commands/{name}", status_code=204)
async def delete_command(name: str):
    db = await get_db()
    cursor = await db.execute("DELETE FROM config_commands WHERE name = ?", (name,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Command not found")
    await db.commit()
