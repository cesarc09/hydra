import json
from pathlib import Path

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from server.auth import require_auth
from server.config import EDITORS_PATH
from server.services.session_manager import (
    get_all_sessions,
    get_session_events,
    subscribe,
    unsubscribe,
)

router = APIRouter(
    prefix="/api", tags=["sessions"], dependencies=[Depends(require_auth)]
)


@router.get("/sessions")
async def list_sessions():
    return await get_all_sessions()


@router.get("/sessions/{session_id}/events")
async def session_events(session_id: str, limit: int = 50):
    return await get_session_events(session_id, limit)


@router.get("/editors")
async def get_editors():
    path = Path(EDITORS_PATH)
    if path.exists():
        return json.loads(path.read_text())
    return {"default": {"editor": "vscode", "type": "local"}, "instances": {}}


@router.get("/events/stream")
async def event_stream():
    queue = subscribe()

    async def generate():
        try:
            while True:
                data = await queue.get()
                yield {"event": "hook_event", "data": json.dumps(data)}
        finally:
            unsubscribe(queue)

    return EventSourceResponse(generate())
