from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryType = Literal["user", "feedback", "project", "reference"]
HookRuntime = Literal["python", "bash"]


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


class RemoteControlUrlUpdate(BaseModel):
    """Body for PUT /api/sessions/{id}/remote-control-url. Empty string clears."""
    url: str = Field(max_length=256)


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
    # Names are globally unique. A POST whose name already exists in a DIFFERENT
    # scope is rejected with 409 unless the caller explicitly opts in to moving
    # it. Without this, any by-name push could silently re-scope a memory some-
    # one deliberately pinned - which is how mirror files resurrected deleted
    # rows in the first place.
    rescope: bool = False


class MemoryUpdate(BaseModel):
    """Partial update - only fields PRESENT in the request body are applied
    (model_dump(exclude_unset=True)), so `{"project_slug": null}` unpins a
    memory to global scope while an omitted project_slug leaves scope alone.
    project_slug is the only nullable field; an explicit null elsewhere is a 422.
    """
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


# --- Distributed hooks ---


class HookUpsert(BaseModel):
    """Body for PUT /api/config/hooks/{name}: a policy hook's script and its
    settings.json wiring, upserted together.

    `event` is not validated against a fixed list - Claude Code adds hook events
    often enough that an allowlist here would reject valid config until the
    server is redeployed. `matcher` is None when the hook should apply to every
    invocation of its event; the client then emits no `matcher` key at all.
    `instances` is None for "every machine", or a list of HYDRA_INSTANCE_ID
    values to restrict it to; the CLIENT filters on it, so this endpoint keeps
    returning the whole fleet's config to any machine that asks.
    """
    content: str = Field(min_length=1)
    runtime: HookRuntime = "python"
    event: str = Field(min_length=1, max_length=64, pattern=r"^\S+$")
    matcher: str | None = Field(default=None, max_length=256)
    timeout: int = Field(default=10, ge=1, le=600)
    enabled: bool = True
    instances: list[str] | None = None


# --- Projects ---


class ProjectCreate(BaseModel):
    slug: str
    path: str
    description: str = ""


class ProjectUpdate(BaseModel):
    """Partial update - only non-None fields are applied."""
    description: str | None = None


class ProjectPath(BaseModel):
    instance_id: str
    path: str
    auto_registered_at: str | None = None


class ProjectItem(BaseModel):
    slug: str
    description: str
    paths: list[ProjectPath] = Field(default_factory=list)
    created_at: str
    updated_at: str
    auto_registered_at: str | None = None


class AutoRegisterRequest(BaseModel):
    """Body for POST /api/projects/auto-register. Server derives the slug from
    the cwd basename and applies the stoplist."""
    cwd: str = Field(min_length=1, max_length=4096)


class AutoRegisterResponse(BaseModel):
    """Status values:
    - "existing": cwd was already registered for this (slug, instance_id).
    - "attached": slug already existed; this machine's path was added.
    - "created": brand-new slug; project + path both created.
    - "skipped": stoplist rejected the derived slug; no write happened.
    """
    status: Literal["existing", "attached", "created", "skipped"]
    slug: str | None = None
    reason: str | None = None
