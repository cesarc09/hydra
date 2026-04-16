from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.config import BASE_DIR
from server.db import close_db, get_db
from server.routers import config, hooks, memory, projects, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_db()  # Initialize DB on startup
    yield
    await close_db()


app = FastAPI(title="Hydra", lifespan=lifespan)

app.include_router(hooks.router)
app.include_router(sessions.router)
app.include_router(config.router)
app.include_router(memory.router)
app.include_router(projects.router)

app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="static")
