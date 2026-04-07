from fastapi import APIRouter

from server.services import memory_sync

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("/status")
async def get_memory_status():
    return memory_sync.status()


@router.post("/sync")
async def trigger_memory_sync():
    return await memory_sync.sync()
