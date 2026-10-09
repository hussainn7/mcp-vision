"""Runs the actions the model asks for.

Safe actions run right away. Consequential ones (sending a message, moving
files, filling a form) build a preview first and wait for the user's yes,
spoken or clicked. Every run is logged.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_vision.buddy.actions.base import (
    ActionContext, ActionError, ActionResult, ActionSpec, Preview, maybe_await,
)

YES_RE = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|go for it|send( it)?|confirm(ed)?|"
                    r"please( do)?|correct|that's right|sounds good|tidy( it)?( up)?|fill( it)?|run it|absolutely|y)\b",
                    re.IGNORECASE)
NO_RE = re.compile(r"^\W*(no|nope|nah|cancel|stop|don't|do not|never ?mind|wait|hold on|not now|n)\b", re.IGNORECASE)


def answer_kind(text: str) -> str:
    """'yes', 'no', or '' for anything else (a new question)."""
    if NO_RE.match(text):
        return "no"
    if YES_RE.match(text):
        return "yes"
    return ""


# The consequential things a step does, by name. A yes covers a step only when both name the same thing.
_DOINGS = {
    "send": r"send|sending",
    "delete": r"delete|deleting|remove|removing|erase|erasing|trash|trashing|discard|discarding|wipe|wiping",
    "submit": r"submit|submitting",
    "post": r"post|posting|publish|publishing",
    "apply": r"apply|applying",
    "tidy": r"tidy|tidying|organi[sz]e|organi[sz]ing|clean(?:ing)? (?:it |them |this |that )?up",
    "fill": r"fill|filling",
    "attach": r"attach|attaching|upload|uploading",
    "quit": r"quit|quitting|cmd\+q",
    "sign out": r"(?:sign|log)(?:ging)? ?out|logout",
    "unsubscribe": r"unsubscribe|unsubscribing",
    "merge": r"merge|merging",
    "deploy": r"deploy|deploying",
}
_DOING_RES = {name: re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE) for name, pattern in _DOINGS.items()}
# Spending money always gets its card, however clearly they said so.
MONEY_RE = re.compile(r"\b(buy|buying|purchas\w*|order|ordering|pay|paying|payment|checkout|check ?out|book|booking|"
                      r"reserv\w*|transfer\w*|donat\w*|subscribe|subscribing|tip)\b|[$€£¥]", re.IGNORECASE)
_HOLD_RE = re.compile(r"\b(don'?t|do not|not|never|no|wait|hold|later|before|after|unless|instead|yet|first)\b",
                      re.IGNORECASE)
_ASKING_RE = re.compile(r"^\W*(should|shall|do(?!\s+(it|that|this|so)\b)|does|did|is|are|was|what|why|how|when|where|"
                        r"which|who|whose)\b", re.IGNORECASE)
_POINTING_RE = re.compile(r"\b(it|that|this|them|these|those)\b", re.IGNORECASE)
# Words that don't say what a step is about: a card's "Click “Send”" names nothing beyond the send itself.
_FILLER = {"the", "and", "for", "you", "your", "want", "should", "shall", "can", "could", "would", "like", "now", "ahead",
           "click", "press", "button", "yes", "with", "into", "from", "this", "that", "them", "these", "those", "its",
           "all", "just", "okay", "sure", "then", "too", "also", "about", "going", "will", "let", "please", "here"}


def _about(text: str) -> set[str]:
    """The words that say what a sentence or a card is about, minus the doing itself and filler."""
    words = {word for word in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(word) > 2 and word not in _FILLER}
    return {word for word in words if not any(pattern.fullmatch(word) for pattern in _DOING_RES.values())}


def doings(text: str) -> set[str]:
    """The consequential things a sentence talks about doing: {"send"}, {"delete", "fill"}, ..."""
    return {name for name, pattern in _DOING_RES.items() if pattern.search(text or "")}


@dataclass
class Consent:
    """A yes the user already gave to Plip's own question, so the confirm card doesn't ask again.

    "Want me to send it?" "Yes." That was the confirmation: the step it described (the next one that
    names the same thing, once, this request) runs without a card. A request in their own words ("send
    it", "apply to this job") is not a yes to anything yet: it gets its one card. Never a step that spends
    money, never a card with its own question, never a yes with a "but", "wait" or "not yet".
    """

    doings: set[str]
    about: set[str] = field(default_factory=set)    # what the question was about ("sara", "draft")
    pointing: bool = False                          # it asked about "it" / "that": the thing just talked about

    @classmethod
    def given(cls, transcript: str, asked: str = "") -> Consent | None:
        text = " ".join((transcript or "").split())
        if not asked or answer_kind(text) != "yes" or _ASKING_RE.match(text):
            return None
        if MONEY_RE.search(asked) or MONEY_RE.search(text):
            return None                                # "want me to send Sam the $20?" still gets its card
        if _HOLD_RE.search(YES_RE.sub("", text, count=1)):
            return None
        found = doings(asked)                          # what Plip asked about, not what they added ("and delete X")
        if not found:
            return None
        question = re.findall(r"[^.!?]*\?", asked)
        return cls(found, _about(asked), bool(_POINTING_RE.search(question[-1] if question else asked)))

    def covers(self, preview: Preview) -> bool:
        """The next card only, matched or not: same doing, and the same thing ("Send to Sara" after "...to Sara.
        want me to send it?"), or a bare button for the "it" they were just asked about. A yes about a filter never
        clicks "Delete account"."""
        text = f"{preview.title} {preview.confirm}"
        doing, self.doings = self.doings, set()       # one card per yes
        if preview.firm or MONEY_RE.search(text) or not doing & doings(text):
            return False
        target = _about(preview.title)
        return bool(target & self.about) if target else self.pointing


@dataclass
class Pending:
    spec: ActionSpec
    args: dict
    preview: Preview
    created: float = field(default_factory=time.monotonic)


@dataclass
class Outcome:
    status: str                    # done | failed | pending | unknown | disabled | cancelled
    spec: ActionSpec | None = None
    args: dict = field(default_factory=dict)
    result: ActionResult | None = None
    preview: Preview | None = None
    message: str = ""
    agreed: bool = False           # it would have asked, but they'd already said yes
    hint: str = ""                 # for the model: what to try instead (ActionError.hint)

    @property
    def label(self) -> str:
        return self.spec.describe(self.args) if self.spec else "That action"


class ActionLog:
    """Append-only JSON lines of what Plip did (no message bodies)."""

    def __init__(self, path: Path | None = None):
        from mcp_vision.paths import state_dir

        self.path = path or state_dir() / "actions.jsonl"

    def add(self, name: str, args: dict, ok: bool, source: str = "voice") -> None:
        safe = {key: value for key, value in args.items() if key not in {"text", "body", "fields", "fact"}}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"name": name, "args": safe, "ok": ok, "source": source,
                                         "at": time.time()}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def entries(self, limit: int = 2000) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


class ActionEngine:
    PENDING_TTL = 120.0

    def __init__(self, ctx: ActionContext, specs: Iterable[ActionSpec] | None = None, *,
                 enabled: Callable[[str], bool] = lambda skill: True, log: ActionLog | None = None,
                 timeout: float = 45.0, undo_path: Path | None = None, source: str = "voice"):
        from mcp_vision.buddy.actions import all_specs

        self.ctx = ctx
        self.specs = {spec.name: spec for spec in (specs if specs is not None else all_specs())}
        self.enabled = enabled
        self.log = log
        self.timeout = timeout
        self.source = source
        self.pending: Pending | None = None
        self.undo_path = undo_path
        if undo_path is not None:
            try:
                ctx.state.setdefault("undo", json.loads(undo_path.read_text()))
            except (OSError, ValueError):
                ctx.state.setdefault("undo", [])

    # -- prompt help -------------------------------------------------------------------
    def catalog(self) -> list[dict[str, Any]]:
        return [{"name": spec.name, "skill": spec.skill, "args": spec.args, "asks_first": spec.asks_first}
                for spec in self.specs.values()]

    # -- running -----------------------------------------------------------------------------
    async def handle(self, name: str, args: dict | None = None, consent: Consent | None = None) -> Outcome:
        """Run an action, or hold it for a yes. ``consent``: a yes they already gave covers it."""
        args = dict(args or {})
        spec = self.specs.get(name)
        if spec is None:
            return Outcome("unknown", args=args, message=f"I don't know how to {name.replace('_', ' ')} yet.")
        if not self.enabled(spec.skill):
            return Outcome("disabled", spec, args, message=f"My {spec.skill} skill is switched off in settings.")
        if spec.preview is not None:
            try:
                preview = await asyncio.wait_for(asyncio.to_thread(_call_sync, spec.preview, self.ctx, args),
                                                 self.timeout)
                preview = await maybe_await(preview)
            except ActionError as exc:
                return Outcome("failed", spec, args, message=str(exc), hint=exc.hint)
            except Exception as exc:
                return Outcome("failed", spec, args, message=_friendly(exc))
            if preview is None:                         # this one needs no yes (a plain click, an ordinary key)
                return await self._run(spec, args)
            if consent is not None and consent.covers(preview):
                outcome = await self._run(spec, args, preview.state)
                outcome.agreed = True
                return outcome
            self.pending = Pending(spec, args, preview)
            return Outcome("pending", spec, args, preview=preview)
        return await self._run(spec, args)

    async def answer(self, accept: bool) -> Outcome | None:
        """The user's yes/no to the pending action. ``None`` when nothing is waiting."""
        pending, self.pending = self.pending, None
        if pending is None or time.monotonic() - pending.created > self.PENDING_TTL:
            return None
        if not accept:
            return Outcome("cancelled", pending.spec, pending.args, message="Okay, I won't.")
        return await self._run(pending.spec, pending.args, pending.preview.state)

    def cancel_pending(self) -> bool:
        had, self.pending = self.pending is not None, None
        return had

    async def _run(self, spec: ActionSpec, args: dict, state: Any = None) -> Outcome:
        runner = spec.run
        call = (lambda: runner(self.ctx, args, state)) if state is not None and _takes_state(runner) else \
            (lambda: runner(self.ctx, args))
        try:
            value = await asyncio.wait_for(asyncio.to_thread(call), self.timeout)
            result = await maybe_await(value)
        except ActionError as exc:
            self._log(spec, args, False)
            return Outcome("failed", spec, args, message=str(exc), hint=exc.hint)
        except asyncio.TimeoutError:
            self._log(spec, args, False)
            return Outcome("failed", spec, args, message="That took too long, so I stopped.")
        except Exception as exc:
            self._log(spec, args, False)
            return Outcome("failed", spec, args, message=_friendly(exc))
        if result is None:
            result = ActionResult()
        if result.undo:
            self.ctx.state.setdefault("undo", []).append(result.undo)
            self.ctx.state["undo"] = self.ctx.state["undo"][-10:]
            self._save_undo()
        elif spec.name == "undo":
            self._save_undo()
        self._log(spec, args, result.ok)
        return Outcome("done" if result.ok else "failed", spec, args, result=result,
                       message="" if result.ok else (result.say or "That didn't work."))

    def _log(self, spec: ActionSpec, args: dict, ok: bool) -> None:
        if self.log is not None:
            self.log.add(spec.name, args, ok, self.source)

    def _save_undo(self) -> None:
        if self.undo_path is None:
            return
        try:
            self.undo_path.parent.mkdir(parents=True, exist_ok=True)
            self.undo_path.write_text(json.dumps(self.ctx.state.get("undo", [])))
        except OSError:
            pass


def _call_sync(fn, *args):
    return fn(*args)


def _takes_state(fn) -> bool:
    import inspect

    try:
        return len(inspect.signature(fn).parameters) >= 3
    except (TypeError, ValueError):
        return False


def _friendly(exc: Exception) -> str:
    from mcp_vision.buddy.actions.host import NotSupported

    if isinstance(exc, NotSupported):
        return str(exc)
    text = str(exc).lower()
    if "not authorized" in text or "not allowed" in text or "-1743" in text or "assistive" in text:
        return "macOS blocked that. Allow Plip under Privacy & Security, then ask again."
    if "can't get" in text or "doesn't understand" in text:
        return "That app didn't understand me."
    return "Something went wrong doing that."
