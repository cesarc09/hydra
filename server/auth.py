from fastapi import Header, HTTPException

from server import config


async def require_auth(authorization: str = Header(default="")):
    """Shared auth dependency. Checks Bearer token if HYDRA_AUTH_TOKEN is set."""
    if config.AUTH_TOKEN and authorization != f"Bearer {config.AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Invalid auth token")
