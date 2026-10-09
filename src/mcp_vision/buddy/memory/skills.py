"""Memory as actions: remember / forget, only when the user's own words ask.

So on-screen text can't plant a fact (it rides in every prompt) or wipe memory.
"""
from __future__ import annotations

import re

from mcp_vision.buddy.actions.base import ActionContext, ActionError, ActionResult, ActionSpec
from mcp_vision.buddy.memory.importers import parse_ai_memory


_REMEMBER = re.compile(r"\b(remember|don'?t forget|keep in mind|note (that|this|down)|make a note|save (that|this)|"
                       r"from now on|always|my name is|i (am|live|work|prefer|like))\b", re.IGNORECASE)
_FORGET = re.compile(r"\b(forget|delete|remove|erase|clear|wipe|don'?t remember|stop remembering)\b", re.IGNORECASE)


def _they_said(ctx: ActionContext, pattern: re.Pattern) -> bool:
    return bool(pattern.search(str(ctx.state.get("said") or "")))


def remember(ctx: ActionContext, args: dict) -> ActionResult:
    if ctx.memory is None:
        raise ActionError("My memory is switched off.")
    if not _they_said(ctx, _REMEMBER):
        raise ActionError("I only save things about you when you ask me to.",
                          hint="don't save anything unless their own words ask you to remember it")
    fact = str(args.get("fact") or "").strip()
    key = str(args.get("key") or "").strip().lower()
    value = str(args.get("value") or "").strip()
    if not fact and not value:
        raise ActionError("What should I remember?")
    parsed = [(key, value)] if key and value else (parse_ai_memory(fact) if ":" in fact else [("note", fact)])
    saved = [ctx.memory.add(k, v, "you") for k, v in parsed]
    ctx.memory.save()
    sensitive = any(item is not None and item.sensitive for item in saved)
    return ActionResult(detail="saved privately" if sensitive else "saved",
                        say="Got it. I'll keep that private and ask before using it." if sensitive else "")


def forget(ctx: ActionContext, args: dict) -> ActionResult:
    if ctx.memory is None:
        raise ActionError("My memory is switched off.")
    if not _they_said(ctx, _FORGET):
        raise ActionError("I only forget things when you ask me to.",
                          hint="don't delete anything from memory unless their own words ask you to")
    words = [word for word in str(args.get("about") or "").lower().split() if len(word) > 2]
    if not words:
        raise ActionError("What should I forget?")
    gone = [fact for fact in list(ctx.memory.facts)
            if all(word in (fact.key + " " + fact.value).lower() for word in words)]
    for fact in gone:
        ctx.memory.remove(fact.id)
    ctx.memory.save()
    if not gone:
        raise ActionError("I didn't have anything saved about that.")
    return ActionResult(detail=f"forgot {len(gone)}")


SPECS = (
    ActionSpec("remember", "memory", "Remembering that", remember, args='{"fact"}'),
    ActionSpec("forget", "memory", "Forgetting {about}", forget, args='{"about"}'),
)
