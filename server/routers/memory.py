from fastapi import APIRouter

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("/status")
async def memory_status():
    # Placeholder for Phase 5
    return {"status": "not_configured", "last_sync": None}


@router.post("/sync")
async def memory_sync():
    # Placeholder for Phase 5
    return {"status": "not_configured", "message": "Memory sync not yet implemented"}
