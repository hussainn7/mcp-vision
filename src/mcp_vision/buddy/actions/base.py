"""What an action is, what it returns, and the context it runs in."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class ActionError(RuntimeError):
    """The action ran but couldn't do what was asked; the message is spoken."""


@dataclass
class Preview:
    """Shown in the island (and read back) before a consequential action runs."""

    title: str                              # "Send to Mom"
    lines: list[str] = field(default_factory=list)
    confirm: str = "Do it"                  # button label
    state: Any = None                       # whatever run() needs (e.g. the computed move plan)


@dataclass
class ActionResult:
    ok: bool = True
    say: str = ""                           # spoken after the action when the model didn't cover it
    report: str = ""                        # handed to the model in a follow-up turn
    look_after: float | None = None         # take a fresh look this many seconds later
    detail: str = ""                        # one line for the island step
    items: list[dict] = field(default_factory=list)    # results to list in the island
    undo: dict | None = None                # how to reverse it (kept by the engine)


@dataclass
class ActionContext:
    """Everything an action may touch. Hosts do the platform work."""

    host: Any
    memory: Any = None
    routines: Any = None
    state: dict[str, Any] = field(default_factory=dict)       # e.g. last file results
    announce: Callable[[str], None] = lambda text: None       # speak + show later (timers)
    schedule: Callable[[float, Callable[[], None]], Any] = lambda delay, fn: None
    screen: Any = None                                         # last screenshots/context, for forms


Runner = Callable[[ActionContext, dict], Awaitable[ActionResult] | ActionResult]
Previewer = Callable[[ActionContext, dict], Awaitable[Preview] | Preview]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    skill: str                    # the Skills toggle it belongs to ("apps", "files", ...)
    label: str                    # island step: "Opening {name}"
    run: Runner
    preview: Previewer | None = None    # set => asks the user first
    args: str = ""                # one-line arg hint for the prompt

    @property
    def asks_first(self) -> bool:
        return self.preview is not None

    def describe(self, args: dict) -> str:
        try:
            return self.label.format(**{key: _short(value) for key, value in args.items()})
        except (KeyError, IndexError, ValueError):
            return self.label.split("{")[0].strip() or self.name


def _short(value: Any, limit: int = 40) -> str:
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def maybe_await(value):
    if hasattr(value, "__await__"):
        return await value
    return value
