"""Bounded context shared by browser and desktop invocation clients."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class Point(BaseModel):
    x: float
    y: float


class ContextBounds(BaseModel):
    x: float
    y: float
    width: float = Field(ge=0)
    height: float = Field(ge=0)


class ContextElement(BaseModel):
    role: str = ""
    name: str = ""
    value: str = ""
    tag: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    bounds: ContextBounds | None = None

    @field_validator("role", "name", "value", "tag")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return str(value or "")[:1000]


class SessionInfo(BaseModel):
    authenticated: bool | None = None
    profile: str = ""
    session_kind: str = ""


class IdentityState(BaseModel):
    status: Literal["unknown", "anonymous", "authenticated", "required"] = "unknown"
    service: str = ""
    account_hint: str = ""


class Context(BaseModel):
    """Enough nearby state to understand an invocation, never a full page dump."""

    context_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: Literal["chrome", "macos", "api", "unknown"] = "unknown"
    source_application: str = ""
    url: str = ""
    title: str = ""
    selected_text: str = ""
    focused_element: ContextElement | None = None
    clicked_element: ContextElement | None = None
    element_bounds: ContextBounds | None = None
    dom_context: dict[str, Any] = Field(default_factory=dict)
    accessibility_context: dict[str, Any] = Field(default_factory=dict)
    screenshot_reference: str = ""
    cursor_position: Point | None = None
    viewport: ContextBounds | None = None
    session: SessionInfo = Field(default_factory=SessionInfo)
    identity: IdentityState = Field(default_factory=IdentityState)
    user_request: str = ""

    @field_validator("source_application", "url", "title", "screenshot_reference")
    @classmethod
    def trim_metadata(cls, value: str) -> str:
        return str(value or "")[:2048]

    @field_validator("selected_text", "user_request")
    @classmethod
    def trim_user_text(cls, value: str) -> str:
        return str(value or "")[:12000]

    def compact(self) -> dict[str, Any]:
        """Return prompt-safe context with empty values removed."""
        data = self.model_dump(mode="json", exclude_none=True)
        data.pop("context_id", None)
        data.pop("created_at", None)
        return _compact(data)


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k)[:100]: _compact(v) for k, v in list(value.items())[:80]
                if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_compact(v) for v in value[:80]]
    if isinstance(value, str):
        return value[:6000]
    return value
