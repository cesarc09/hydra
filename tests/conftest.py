from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

import server.db as db_module
from server.app import app

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Async HTTP client with a fresh isolated database per test."""
    db_path = str(tmp_path / "test.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(SCHEMA_PATH.read_text())
    await conn.commit()

    # Inject test DB — get_db() reads _db from the module, so all importers see it
    monkeypatch.setattr(db_module, "_db", conn)
    # Disable auth for all tests by default
    monkeypatch.setattr("server.config.AUTH_TOKEN", "")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await conn.close()
