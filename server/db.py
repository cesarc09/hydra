import aiosqlite

from server.config import DB_PATH, BASE_DIR

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        # Initialize schema
        schema_path = BASE_DIR / "schema.sql"
        await _db.executescript(schema_path.read_text())
        await _db.commit()
    return _db


async def close_db():
    global _db
    if _db is not None:
        await _db.close()
        _db = None
