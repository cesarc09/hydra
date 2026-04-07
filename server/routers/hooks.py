from fastapi import APIRouter, Header, HTTPException, Request

from server.config import AUTH_TOKEN
from server.models import HookEvent
from server.services.session_manager import handle_event

router = APIRouter(prefix="/api/hooks", tags=["hooks"])


@router.post("/event")
async def receive_hook_event(
    request: Request,
    x_instance_id: str = Header(default="unknown"),
    authorization: str = Header(default=""),
):
    # Validate auth token
    if AUTH_TOKEN:
        expected = f"Bearer {AUTH_TOKEN}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Invalid auth token")

    body = await request.json()
    event = HookEvent(**body)
    await handle_event(event, instance_id=x_instance_id)
    return {}
