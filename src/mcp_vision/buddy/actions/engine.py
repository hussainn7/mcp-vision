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
    async def handle(self, name: str, args: dict | None = None) -> Outcome:
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
                return Outcome("failed", spec, args, message=str(exc))
            except Exception as exc:
                return Outcome("failed", spec, args, message=_friendly(exc))
            if preview is None:                         # this one needs no yes (a plain click, an ordinary key)
                return await self._run(spec, args)
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
            return Outcome("failed", spec, args, message=str(exc))
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
