"""Things Plip can do on the Mac, requested by the model with ``[DO:name {json}]`` tags."""
from __future__ import annotations

from mcp_vision.buddy.actions.base import ActionContext, ActionError, ActionResult, ActionSpec, Preview
from mcp_vision.buddy.actions.engine import ActionEngine, ActionLog, Outcome, answer_kind


def all_specs() -> list[ActionSpec]:
    from mcp_vision.buddy.actions import core
    from mcp_vision.buddy.memory import skills as memory_skills

    return [*core.SPECS, *memory_skills.SPECS]


SKILLS = {
    "apps": ("Apps & web", "Open apps, links and searches"),
    "files": ("Files & desktop", "Find files with Spotlight, tidy your desktop"),
    "system": ("Mac controls", "Dark mode, volume, your Apple Shortcuts"),
    "writing": ("Writing", "Type for you, rewrite the selected text"),
    "planning": ("Reminders & timers", "Reminders, notes and timers"),
    "travel": ("Travel", "Find flights on Google Flights"),
    "memory": ("Memory", "Remember what you tell it, use your details"),
}

__all__ = ["SKILLS", "ActionContext", "ActionEngine", "ActionError", "ActionLog", "ActionResult", "ActionSpec",
           "Outcome", "Preview", "all_specs", "answer_kind"]
