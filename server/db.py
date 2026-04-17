import aiosqlite

from server.config import BASE_DIR, DB_PATH

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        # Initialize schema
        schema_path = BASE_DIR / "schema.sql"
        await _db.executescript(schema_path.read_text())
        await _migrate(_db)
        await _db.commit()
    return _db


async def _migrate(conn: aiosqlite.Connection) -> None:
    """Idempotent in-place migrations for existing DBs.

    schema.sql uses CREATE TABLE IF NOT EXISTS, so new columns on pre-existing
    tables don't appear without an ALTER. Keep this short — each block should
    check for its target state before acting.
    """
    cursor = await conn.execute("PRAGMA table_info(memories)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "project_slug" not in cols:
        await conn.execute(
            "ALTER TABLE memories ADD COLUMN project_slug TEXT "
            "REFERENCES projects(slug) ON DELETE SET NULL"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_global "
            "ON memories(name) WHERE project_slug IS NULL"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_project "
            "ON memories(name, project_slug) WHERE project_slug IS NOT NULL"
        )


async def close_db():
    global _db
    if _db is not None:
        await _db.close()
        _db = None
