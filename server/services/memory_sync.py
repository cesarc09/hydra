import asyncio
import subprocess
from datetime import datetime, timezone

from server.config import CONFIG_REPO_PATH

_last_sync: str | None = None
_last_error: str | None = None


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
    )


async def sync() -> dict:
    """Pull latest changes in the claude-config repo on the server."""
    global _last_sync, _last_error

    if not CONFIG_REPO_PATH:
        return {"status": "not_configured", "message": "HYDRA_CONFIG_REPO not set"}

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, _run_git, ["pull", "--ff-only"], CONFIG_REPO_PATH
        )
        _last_sync = datetime.now(timezone.utc).isoformat()

        if result.returncode == 0:
            _last_error = None
            return {
                "status": "ok",
                "message": result.stdout.strip() or "Already up to date.",
                "last_sync": _last_sync,
            }
        else:
            _last_error = result.stderr.strip()
            return {
                "status": "error",
                "message": _last_error,
                "last_sync": _last_sync,
            }
    except subprocess.TimeoutExpired:
        _last_error = "Git pull timed out (15s)"
        return {"status": "error", "message": _last_error}
    except FileNotFoundError:
        _last_error = f"Repo not found: {CONFIG_REPO_PATH}"
        return {"status": "error", "message": _last_error}


def status() -> dict:
    if not CONFIG_REPO_PATH:
        return {"status": "not_configured", "repo": None, "last_sync": None, "last_error": None}
    return {
        "status": "ok" if _last_error is None else "error",
        "repo": CONFIG_REPO_PATH,
        "last_sync": _last_sync,
        "last_error": _last_error,
    }
