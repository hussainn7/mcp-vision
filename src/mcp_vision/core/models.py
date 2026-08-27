from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Policy(str, Enum):
    SAFE_READ = "SAFE_READ"
    ROUTINE_WRITE = "ROUTINE_WRITE"
    RESTRICTED_ACTION = "RESTRICTED_ACTION"


class BoundingBox(BaseModel):
    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2


class ScreenElement(BaseModel):
    id: int
    label: str
    role: str = "unknown"
    bbox: BoundingBox
    cx: int
    cy: int
    text: str = ""


class ScreenInspectionResult(BaseModel):
    display_id: int
    width: int
    height: int
    scale: float = 1.0
    elements: list[ScreenElement] = Field(default_factory=list)
    png: bytes = b""
    source: str = "visual"

    def element(self, element_id: int) -> ScreenElement | None:
        for el in self.elements:
            if el.id == element_id:
                return el
        return None


class ActionResult(BaseModel):
    ok: bool
    message: str
    element_id: int | None = None
    confirmed: bool = True
    policy: Policy = Policy.SAFE_READ
    extra: dict[str, Any] = Field(default_factory=dict)
