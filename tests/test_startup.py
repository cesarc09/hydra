"""Startup guard: server must refuse to start without auth configured."""

import pytest

from server import config
from server.app import app, lifespan

pytestmark = pytest.mark.asyncio


async def test_startup_raises_when_auth_token_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "AUTH_TOKEN", "")
    monkeypatch.setattr(config, "ALLOW_NO_AUTH", False)
    with pytest.raises(RuntimeError, match="HYDRA_AUTH_TOKEN"):
        async with lifespan(app):
            pass
