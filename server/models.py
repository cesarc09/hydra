from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryType = Literal["user", "feedback", "project", "reference"]


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


# --- Memory ---


class MemoryCreate(BaseModel):
    name: str
    description: str = ""
    type: MemoryType
    body: str = ""
    project_slug: str | None = None


class MemoryUpdate(BaseModel):
    """Partial update — only non-None fields are applied."""
    name: str | None = None
    description: str | None = None
    type: MemoryType | None = None
    body: str | None = None
    project_slug: str | None = None


class MemoryItem(BaseModel):
    id: int
    name: str
    description: str
    type: MemoryType
    body: str
    project_slug: str | None = None
    created_at: str
    updated_at: str


# --- Projects ---


class ProjectCreate(BaseModel):
    slug: str
    path: str
    description: str = ""


class ProjectUpdate(BaseModel):
    """Partial update — only non-None fields are applied."""
    description: str | None = None


class ProjectPath(BaseModel):
    instance_id: str
    path: str


class ProjectItem(BaseModel):
    slug: str
    description: str
    paths: list[ProjectPath] = Field(default_factory=list)
    created_at: str
    updated_at: str
