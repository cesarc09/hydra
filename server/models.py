from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HookEvent(BaseModel):
    """Incoming hook event from a Claude Code instance."""
    model_config = ConfigDict(extra="allow")

    session_id: str
    hook_event_name: str
    cwd: str = ""
    transcript_path: str = ""
    permission_mode: str = ""
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_response: Any = None
    error: str | None = None
    source: str | None = None  # SessionStart source
    model: str | None = None
    message: str | None = None  # Notification message
    notification_type: str | None = None
    agent_type: str | None = None


class SessionState(BaseModel):
    session_id: str
    instance_id: str
    status: str = "active"
    cwd: str = ""
    model: str | None = None
    started_at: str = ""
    last_event_at: str = ""
    last_tool: str | None = None
    last_tool_input_summary: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    end_reason: str | None = None


class EventRecord(BaseModel):
    id: int
    session_id: str
    instance_id: str
    event_name: str
    tool_name: str | None = None
    tool_input_summary: str | None = None
    received_at: str = ""
