from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server import config
from server.db import close_db, get_db
from server.routers import config as config_router
from server.routers import hooks, memory, projects, sessions

# Per-path request body caps. Pi memory is the limiting resource; `tool_input`
# is an unbounded dict otherwise.
_BODY_LIMITS: dict[str, int] = {
    "/api/hooks/event": 64 * 1024,
    "/api/config/claude-md": 1024 * 1024,
}
_DEFAULT_BODY_LIMIT = 256 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not config.AUTH_TOKEN and not config.ALLOW_NO_AUTH:
        raise RuntimeError(
            "HYDRA_AUTH_TOKEN is required. Set HYDRA_ALLOW_NO_AUTH=1 for local dev."
        )
    await get_db()
    yield
    await close_db()


app = FastAPI(title="Hydra", lifespan=lifespan)


@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    limit = _BODY_LIMITS.get(request.url.path, _DEFAULT_BODY_LIMIT)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > limit:
                return JSONResponse({"detail": "Request body too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid content-length"}, status_code=400)
    return await call_next(request)


if config.PUBLIC_ORIGIN:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[f"https://{config.PUBLIC_ORIGIN}"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )


app.include_router(hooks.router)
app.include_router(sessions.router)
app.include_router(config_router.router)
app.include_router(memory.router)
app.include_router(projects.router)


@app.get("/memory")
async def memory_dashboard():
    return FileResponse(config.BASE_DIR / "static" / "memory.html")


app.mount(
    "/", StaticFiles(directory=str(config.BASE_DIR / "static"), html=True), name="static"
)
