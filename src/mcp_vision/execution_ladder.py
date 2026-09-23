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


class RefusalReason(str, Enum):
    STALE_CAPTURE = "stale_capture"
    WINDOW_AUTHORITY_MISSING = "window_authority_missing"
    WINDOW_NOT_FOUND = "window_not_found"
    WINDOW_HIDDEN = "window_hidden"
    WINDOW_MINIMIZED = "window_minimized"
    WINDOW_MOVED = "window_moved"
    SAME_PID_SIBLING_AMBIGUOUS = "same_pid_sibling_ambiguous"
    TARGET_CHANGED = "target_changed"
    FOREGROUND_NOT_AUTHORIZED = "foreground_not_authorized"
    ACTIVATION_FAILED = "activation_failed"
    FRONTMOST_MISMATCH = "frontmost_mismatch"
    PIXELS_CHANGED = "pixels_changed"
    DELIVERY_UNKNOWN_NO_FALLBACK = "delivery_unknown_no_fallback"


class ExecutionAttempt(BaseModel):
    method: ExecutionMethod
    outcome: AttemptOutcome
    background: bool
    detail: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExecutionTrace(BaseModel):
    attempts: list[ExecutionAttempt] = Field(default_factory=list)
    target_authority: dict[str, Any] = Field(default_factory=dict)
    focus: dict[str, Any] = Field(default_factory=dict)
    refusal_reason: RefusalReason | None = None
    successor_observation: dict[str, Any] | None = None

    def add(self, method: ExecutionMethod, outcome: AttemptOutcome, *,
            background: bool, detail: str = "", evidence: dict[str, Any] | None = None) -> None:
        self.attempts.append(ExecutionAttempt(method=method, outcome=outcome, background=background,
                                               detail=detail, evidence=evidence or {}))
        self.focus.setdefault("requested", not background)
        self.focus.setdefault("behavior", "not_requested" if background else "requested")

    def bind_authority(self, authority: dict[str, Any]) -> None:
        self.target_authority = dict(authority)

    def refuse(self, reason: RefusalReason) -> None:
        self.refusal_reason = reason

    def observe(self, **observation: Any) -> None:
        """Attach the immediate read-back, or state that a fresh observation is required."""
        self.successor_observation = dict(observation)

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
            "delivery_outcome": (self.attempts[-1].outcome.value if self.attempts else "didnt"),
            "attempts": [attempt.model_dump(mode="json") for attempt in self.attempts],
            "target_authority": self.target_authority,
            "focus": self.focus,
            "refusal_reason": self.refusal_reason.value if self.refusal_reason else None,
            "successor_observation": (
                self.successor_observation
                if self.successor_observation is not None
                else ({"status": "required"} if self.attempts else None)
            ),
        }
