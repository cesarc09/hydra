from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

import server.routers.hooks as hooks_module
import server.services.session_manager as session_manager_module
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

    async def _get_test_db():
        return conn

    # Patch where get_db is actually called (imported name in session_manager)
    monkeypatch.setattr(session_manager_module, "get_db", _get_test_db)
    monkeypatch.setattr(hooks_module, "AUTH_TOKEN", "")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await conn.close()
