import secrets

from fastapi import Header, HTTPException, Query

from server import config


def _check_no_token() -> bool:
    """Return True if auth is intentionally disabled (dev only); raise if misconfigured."""
    if config.AUTH_TOKEN:
        return False
    if config.ALLOW_NO_AUTH:
        return True
    raise HTTPException(
        status_code=503,
        detail="Server misconfigured: HYDRA_AUTH_TOKEN is required",
    )


def _validate(provided: str):
    if not provided or not secrets.compare_digest(provided, config.AUTH_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid auth token")


def _token_from_header(authorization: str) -> str:
    if authorization.startswith("Bearer "):
        return authorization[len("Bearer "):]
    return ""


async def require_auth(authorization: str = Header(default="")):
    """Bearer token auth for all endpoints."""
    if _check_no_token():
        return
    _validate(_token_from_header(authorization))


async def require_auth_sse(
    authorization: str = Header(default=""),
    token: str = Query(default=""),
):
    """SSE-only: accept token via Authorization header OR ?token= query param.

    EventSource cannot set custom headers, so the SSE route must accept the
    token as a query parameter. Scoped to this one endpoint to avoid the
    token leaking into access logs for arbitrary requests.
    """
    if _check_no_token():
        return
    _validate(_token_from_header(authorization) or token)
