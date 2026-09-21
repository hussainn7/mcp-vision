"""Honest execution-path accounting shared by browser and native backends."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionMethod(str, Enum):
    NATIVE_API = "native_api"
    DOM = "dom"
    CDP = "cdp"
    AX_BACKGROUND = "ax_background"
    AX_FOREGROUND = "ax_foreground"
    PID_KEYBOARD = "pid_keyboard"
    FOREGROUND_KEYBOARD = "foreground_keyboard"
    FOREGROUND_POINTER = "foreground_pointer"
    VISUAL_POINTER = "visual_pointer"


class AttemptOutcome(str, Enum):
    WORKED = "worked"
    DIDNT = "didnt"
    UNKNOWN = "unknown"


class ExecutionAttempt(BaseModel):
    method: ExecutionMethod
    outcome: AttemptOutcome
    background: bool
    detail: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExecutionTrace(BaseModel):
    attempts: list[ExecutionAttempt] = Field(default_factory=list)

    def add(self, method: ExecutionMethod, outcome: AttemptOutcome, *,
            background: bool, detail: str = "", evidence: dict[str, Any] | None = None) -> None:
        self.attempts.append(ExecutionAttempt(method=method, outcome=outcome, background=background,
                                              detail=detail, evidence=evidence or {}))

    @property
    def chosen(self) -> ExecutionMethod | None:
        terminal = next((attempt for attempt in reversed(self.attempts)
                         if attempt.outcome is not AttemptOutcome.DIDNT), None)
        return terminal.method if terminal else None

    @property
    def background(self) -> bool | None:
        terminal = next((attempt for attempt in reversed(self.attempts)
                         if attempt.outcome is not AttemptOutcome.DIDNT), None)
        return terminal.background if terminal else None

    @property
    def can_fallback(self) -> bool:
        """Retry only after a checked no-effect result, never an unknown delivery."""
        return not self.attempts or self.attempts[-1].outcome is AttemptOutcome.DIDNT

    def evidence(self) -> dict[str, Any]:
        return {
            "execution_path": self.chosen.value if self.chosen else None,
            "background": self.background,
            "attempts": [attempt.model_dump(mode="json") for attempt in self.attempts],
        }
