"""Routines: teach Plip "when I say X, do Y", and let it notice the ones you already do.

A routine is a phrase plus a list of safe actions (open apps and links, run
Shortcuts, change settings). Saying the phrase runs it instantly, without a
model call. Plip also mines its own action log for sets of things you do
together on several different days and suggests them as routines.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from mcp_vision.buddy.actions.base import ActionContext, ActionError, ActionResult, ActionSpec
from mcp_vision.buddy.store import config_dir

ROUTINE_ACTIONS = {"open_app", "open_url", "web_search", "run_shortcut", "system", "set_timer", "create_note",
                   "create_reminder", "type_text"}
LEARNABLE = {"open_app", "open_url", "run_shortcut", "system"}
SESSION_GAP = 10 * 60
MIN_DAYS = 3


def normalize_phrase(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


@dataclass
class Routine:
    name: str
    phrase: str
    steps: list[dict]
    source: str = "taught"             # taught | suggested
    runs: int = 0
    created: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def describe(self) -> str:
        return ", ".join(describe_step(step) for step in self.steps)


def describe_step(step: dict) -> str:
    args = step.get("args") or {}
    name = step.get("name", "")
    if name == "open_app":
        return f"open {args.get('name', 'an app')}"
    if name == "open_url":
        return f"open {args.get('url', 'a link')}"
    if name == "run_shortcut":
        return f"run the {args.get('name', '')} shortcut"
    if name == "system":
        return f"set {str(args.get('setting', '')).replace('_', ' ')} {args.get('value', '')}".strip()
    return name.replace("_", " ")


def validate_steps(steps) -> list[dict]:
    if not isinstance(steps, list) or not steps:
        raise ActionError("A routine needs at least one step.")
    clean = []
    for step in steps[:12]:
        if not isinstance(step, dict) or step.get("name") not in ROUTINE_ACTIONS:
            name = step.get("name") if isinstance(step, dict) else step
            raise ActionError(f"Routines can't include {str(name).replace('_', ' ')}; I only run safe steps on my own.")
        clean.append({"name": step["name"], "args": dict(step.get("args") or {})})
    return clean


class Routines:
    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "routines.json"
        self.items: list[Routine] = []
        self.dismissed: list[str] = []
        try:
            data = json.loads(self.path.read_text())
            self.items = [Routine(**item) for item in data.get("routines", [])]
            self.dismissed = data.get("dismissed", [])
        except (OSError, ValueError, TypeError):
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"routines": [asdict(item) for item in self.items], "dismissed": self.dismissed},
                                  indent=1))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def add(self, name: str, phrase: str, steps, source: str = "taught") -> Routine:
        name = " ".join(str(name).split())[:60] or "My routine"
        phrase = normalize_phrase(phrase or name)
        if not phrase:
            raise ActionError("What should I listen for to start it?")
        steps = validate_steps(steps)
        self.items = [item for item in self.items if item.phrase != phrase and item.name.lower() != name.lower()]
        routine = Routine(name, phrase, steps, source)
        self.items.append(routine)
        return routine

    def remove(self, routine_id: str) -> bool:
        before = len(self.items)
        self.items = [item for item in self.items if item.id != routine_id]
        return len(self.items) != before

    def find(self, name: str) -> Routine | None:
        wanted = normalize_phrase(name)
        return next((item for item in self.items if wanted in {normalize_phrase(item.name), item.phrase}), None)

    def match(self, transcript: str) -> Routine | None:
        """The routine whose phrase the user just said (exactly, optionally with 'plip' / 'please')."""
        said = normalize_phrase(transcript)
        said = re.sub(r"^(hey |ok |okay )?plip,? ", "", said)
        said = re.sub(r"( please| now| plip)+$", "", said)
        return next((item for item in self.items if item.phrase == said), None)

    def summary(self) -> str:
        if not self.items:
            return ""
        lines = ["your saved routines (when the user says one, run it with run_routine):"]
        lines += [f'- "{item.phrase}" ({item.name}): {item.describe()}' for item in self.items[:15]]
        return "\n".join(lines)

    def cards(self) -> list[dict]:
        return [{"id": item.id, "name": item.name, "phrase": item.phrase, "steps": [describe_step(s) for s in item.steps],
                 "runs": item.runs, "source": item.source} for item in self.items]


# -- learning from the action log ------------------------------------------------------------------

def _item(entry: dict) -> str | None:
    if not entry.get("ok") or entry.get("name") not in LEARNABLE:
        return None
    args = entry.get("args") or {}
    if entry["name"] == "open_app" and args.get("name"):
        return f"open_app:{str(args['name']).strip().title()}"
    if entry["name"] == "open_url" and args.get("url"):
        return f"open_url:{args['url']}"
    if entry["name"] == "run_shortcut" and args.get("name"):
        return f"run_shortcut:{args['name']}"
    if entry["name"] == "system" and args.get("setting"):
        return f"system:{args['setting']}={args.get('value', '')}"
    return None


def _step(item: str) -> dict:
    name, _, value = item.partition(":")
    if name == "system":
        setting, _, setting_value = value.partition("=")
        return {"name": "system", "args": {"setting": setting, "value": setting_value}}
    key = "url" if name == "open_url" else "name"
    return {"name": name, "args": {key: value}}


def suggest_routines(entries: list[dict], existing: list[Routine] = (), dismissed: list[str] = (),
                     limit: int = 3) -> list[dict]:
    """Sets of actions done together (within 10 minutes) on at least three different days."""
    sessions: list[tuple[float, list[str]]] = []
    current: list[str] = []
    started = last = None
    for entry in sorted(entries, key=lambda item: item.get("at", 0)):
        item = _item(entry)
        if item is None:
            continue
        at = float(entry.get("at", 0))
        if last is None or at - last > SESSION_GAP:
            if current:
                sessions.append((started, current))
            current, started = [], at
        if item not in current:
            current.append(item)
        last = at
    if current:
        sessions.append((started, current))

    days: dict[frozenset, set[str]] = defaultdict(set)
    hours: dict[frozenset, list[float]] = defaultdict(list)
    order: dict[str, list[int]] = defaultdict(list)
    for started, items in sessions:
        day = datetime.fromtimestamp(started).strftime("%Y-%m-%d")
        moment = datetime.fromtimestamp(started)
        for position, item in enumerate(items):
            order[item].append(position)
        for size in range(2, min(4, len(items)) + 1):
            for combo in itertools.combinations(items[:6], size):
                key = frozenset(combo)
                if day not in days[key]:
                    days[key].add(day)
                    hours[key].append(moment.hour + moment.minute / 60)

    taken = [frozenset(_item({"ok": True, **step}) for step in routine.steps) for routine in existing]
    taken += [frozenset(signature.split("|")) for signature in dismissed]     # and none of their subsets
    chosen: list[frozenset] = []
    for key in sorted((key for key, seen in days.items() if len(seen) >= MIN_DAYS),
                      key=lambda key: (-len(key), -len(days[key]))):
        if any(key <= other for other in chosen) or any(key <= other for other in taken):
            continue
        chosen.append(key)
        if len(chosen) >= limit:
            break

    suggestions = []
    for key in chosen:
        items = sorted(key, key=lambda item: statistics.median(order[item]) if order[item] else 0)
        hour = statistics.median(hours[key])
        name = ("Morning setup" if hour < 11 else "Afternoon focus" if hour < 17 else "Evening wind-down")
        phrase = {"Morning setup": "start my day", "Afternoon focus": "focus time",
                  "Evening wind-down": "wind down"}[name]
        steps = [_step(item) for item in items]
        suggestions.append({"key": "|".join(sorted(key)), "name": name, "phrase": phrase, "steps": steps,
                            "labels": [describe_step(step) for step in steps], "days": len(days[key]),
                            "around": f"{int(hour) % 12 or 12}:{int((hour % 1) * 60):02d} {'AM' if hour < 12 else 'PM'}"})
    return suggestions


# -- actions ------------------------------------------------------------------------------------------

def save_routine(ctx: ActionContext, args: dict) -> ActionResult:
    if ctx.routines is None:
        raise ActionError("Routines are switched off.")
    routine = ctx.routines.add(args.get("name") or args.get("phrase") or "", args.get("phrase") or "",
                               args.get("steps"))
    ctx.routines.save()
    return ActionResult(say=f'Saved. Say "{routine.phrase}" any time and I\'ll {routine.describe()}.',
                        detail=routine.name)


def run_routine(ctx: ActionContext, args: dict) -> ActionResult:
    if ctx.routines is None:
        raise ActionError("Routines are switched off.")
    routine = ctx.routines.find(str(args.get("name") or ""))
    if routine is None:
        raise ActionError("I don't have a routine by that name yet.")
    specs = ctx.state.get("specs") or {}
    done, failed = [], []
    for step in routine.steps:
        spec = specs.get(step["name"])
        if spec is None or spec.asks_first:
            failed.append(describe_step(step))
            continue
        try:
            spec.run(ctx, dict(step.get("args") or {}))
            done.append(describe_step(step))
        except Exception:
            failed.append(describe_step(step))
    routine.runs += 1
    ctx.routines.save()
    say = "" if not failed else f"I couldn't {', '.join(failed)}."
    return ActionResult(detail=f"{len(done)} of {len(routine.steps)} steps", say=say)


SPECS = (
    ActionSpec("save_routine", "routines", "Saving routine {name}", save_routine,
               args='{"name", "phrase", "steps": [{"name", "args"}]}'),
    ActionSpec("run_routine", "routines", "Running {name}", run_routine, args='{"name"}'),
)
