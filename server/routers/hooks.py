from fastapi import APIRouter, Depends, Header, Request

from server.auth import require_auth
from server.models import HookEvent
from server.services.session_manager import handle_event

router = APIRouter(prefix="/api/hooks", tags=["hooks"], dependencies=[Depends(require_auth)])


@router.post("/event")
async def receive_hook_event(
    request: Request,
    x_instance_id: str = Header(default="unknown"),
):
    body = await request.json()
    event = HookEvent(**body)
    await handle_event(event, instance_id=x_instance_id)
    return {}
