"""Immutable, backend-neutral UI observations and bounded action spaces.

The model may choose among actions, but this module defines what exists and
what can be executed from a particular observation.  Element references are
state scoped: ``@e3`` from one state is never resolved against another state.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from enum import Enum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from mcp_vision.core.models import BoundingBox, Policy


class Operation(str, Enum):
    PRESS = "press"
    TYPE = "type"
    SELECT = "select"
    SET_CHECKED = "set_checked"
    SCROLL = "scroll"
    WAIT = "wait"
    REOBSERVE = "reobserve"
    REPLAN = "replan"


class ElementIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    dom: str | None = None
    accessibility: str | None = None
    visual: str | None = None


class UIElement(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: str
    state_id: str
    root_id: str
    role: str
    name: str = ""
    value: str = ""
    description: str = ""
    bounds: BoundingBox
    identity: ElementIdentity = Field(default_factory=ElementIdentity)
    visible: bool = True
    occluded: bool = False
    freshness: str = "observed"
    confidence: float = 1.0
    capabilities: tuple[Operation, ...] = ()
    risk: Policy = Policy.SAFE_READ
    sources: tuple[str, ...] = ()
    checked: bool | None = None
    options: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    state_id: str
    operation: Operation
    label: str
    target_ref: str | None = None
    target_index: int | None = Field(default=None, exclude=True)
    argument: str | None = None
    risk: Policy = Policy.SAFE_READ
    requires_confirmation: bool = False


class UIState(BaseModel):
    model_config = ConfigDict(frozen=True)

    state_id: str
    root_id: str
    epoch: int
    source: str
    observed_at: float
    url: str = ""
    title: str = ""
    text: str = ""
    content_hash: str
    elements: tuple[UIElement, ...] = ()
    candidates: tuple[ActionCandidate, ...] = ()
    pruned: dict[str, int] = Field(default_factory=dict)

    def element(self, ref: str) -> UIElement | None:
        return next((element for element in self.elements if element.ref == ref), None)

    def candidate(self, candidate_id: str) -> ActionCandidate | None:
        return next((item for item in self.candidates if item.id == candidate_id), None)


class ElementChange(BaseModel):
    ref: str
    fields: tuple[str, ...]


class StateDiff(BaseModel):
    before_state_id: str
    after_state_id: str
    root_changed: bool = False
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    updated: tuple[ElementChange, ...] = ()
    url_changed: bool = False
    title_changed: bool = False
    text_changed: bool = False

    @property
    def changed(self) -> bool:
        return any((self.root_changed, self.added, self.removed, self.updated,
                    self.url_changed, self.title_changed, self.text_changed))


def _root_id(snapshot: Any) -> str:
    if getattr(snapshot, "root_id", ""):
        return snapshot.root_id
    root = snapshot.url or snapshot.title or snapshot.source
    digest = hashlib.sha256(f"{snapshot.source}\0{root}".encode()).hexdigest()[:16]
    return f"root-{digest}"


def _capabilities(record: dict[str, Any]) -> tuple[Operation, ...]:
    role = str(record.get("role") or "").lower()
    input_type = str(record.get("input_type") or "").lower()
    caps: list[Operation] = []
    # Labels contribute accessible names but are not independent semantic
    # actions; clicking them can toggle/focus a different control implicitly.
    if role in {"button", "link", "menuitem", "tab", "option"}:
        caps.append(Operation.PRESS)
    if role in {"textbox", "searchbox", "spinbutton"} and input_type not in {"checkbox", "radio", "file"}:
        caps.append(Operation.TYPE)
    if role in {"combobox", "listbox"} or record.get("options"):
        caps.append(Operation.SELECT)
    if role in {"checkbox", "radio", "switch"}:
        caps.append(Operation.SET_CHECKED)
    return tuple(dict.fromkeys(caps))


def _risk(record: dict[str, Any], operation: Operation) -> Policy:
    name = str(record.get("name") or "").lower()
    input_type = str(record.get("input_type") or "").lower()
    if input_type in {"password", "file"} or record.get("submits"):
        return Policy.RESTRICTED_ACTION
    if operation is Operation.PRESS and any(word in name for word in (
        "submit", "send", "purchase", "buy", "book", "delete", "remove", "accept", "confirm",
    )):
        return Policy.RESTRICTED_ACTION
    return Policy.ROUTINE_WRITE


def _identity(record: dict[str, Any], source: str) -> ElementIdentity:
    raw = record.get("identity") or {}
    dom = raw.get("dom") if isinstance(raw, dict) else None
    ax = raw.get("accessibility") if isinstance(raw, dict) else None
    if source.startswith("macos") and not ax:
        ax = record.get("ax_ref")
    return ElementIdentity(dom=dom, accessibility=ax, visual=raw.get("visual") if isinstance(raw, dict) else None)


def _fingerprint(snapshot: Any) -> str:
    payload = {
        "root": (snapshot.source, snapshot.url, snapshot.title),
        "text": snapshot.text,
        "elements": [
            {key: record.get(key) for key in ("role", "name", "value", "checked", "x", "y", "w", "h")}
            for record in snapshot.elements
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def compile_state(snapshot: Any, *, epoch: int) -> UIState:
    """Normalize a BrowserSnapshot-compatible value into an immutable state."""
    state_id = snapshot.snapshot_id
    root_id = _root_id(snapshot)
    elements: list[UIElement] = []
    candidates: list[ActionCandidate] = []
    for record in snapshot.elements:
        index = int(record["index"])
        ref = f"@e{index}"
        box = BoundingBox(x=round(record.get("x", 0)), y=round(record.get("y", 0)),
                          w=round(record.get("w", 0)), h=round(record.get("h", 0)))
        capabilities = _capabilities(record)
        element_risk = max((_risk(record, cap) for cap in capabilities),
                           key=lambda value: list(Policy).index(value), default=Policy.SAFE_READ)
        element = UIElement(
            ref=ref, state_id=state_id, root_id=root_id,
            role=str(record.get("role") or "unknown"), name=str(record.get("name") or ""),
            value=str(record.get("value") or ""), description=str(record.get("description") or ""),
            bounds=box, identity=_identity(record, snapshot.source), capabilities=capabilities,
            risk=element_risk, sources=(snapshot.source,), checked=record.get("checked"),
            options=tuple(record.get("options") or ()),
            metadata={key: record[key] for key in ("tag", "input_type", "required", "valid") if key in record},
        )
        elements.append(element)
        for operation in capabilities:
            risk = _risk(record, operation)
            candidates.append(ActionCandidate(
                id=f"A{len(candidates) + 1}", state_id=state_id, operation=operation,
                label=f"{operation.value.upper()} {ref} {element.name or element.role}".strip(),
                target_ref=ref, target_index=index,
                argument={Operation.TYPE: "text", Operation.SELECT: "value",
                          Operation.SET_CHECKED: "checked"}.get(operation),
                risk=risk, requires_confirmation=risk is Policy.RESTRICTED_ACTION,
            ))
    for operation, label, argument in (
        (Operation.SCROLL, "SCROLL page", "delta_y"),
        (Operation.WAIT, "WAIT for UI to settle", "milliseconds"),
        (Operation.REOBSERVE, "REOBSERVE current root", None),
        (Operation.REPLAN, "REPLAN with System-2", None),
    ):
        candidates.append(ActionCandidate(id=f"A{len(candidates) + 1}", state_id=state_id,
                                          operation=operation, label=label, argument=argument))
    return UIState(
        state_id=state_id, root_id=root_id, epoch=epoch, source=snapshot.source,
        observed_at=time.time(), url=snapshot.url, title=snapshot.title, text=snapshot.text,
        content_hash=_fingerprint(snapshot), elements=tuple(elements), candidates=tuple(candidates),
        pruned=dict(snapshot.pruned),
    )


def _match_key(element: UIElement) -> tuple[Any, ...]:
    identity = element.identity.dom or element.identity.accessibility or element.identity.visual
    return ("identity", identity) if identity else ("semantic", element.role, element.name)


def diff_states(before: UIState, after: UIState) -> StateDiff:
    if before.root_id != after.root_id:
        return StateDiff(before_state_id=before.state_id, after_state_id=after.state_id,
                         root_changed=True, added=tuple(e.ref for e in after.elements),
                         removed=tuple(e.ref for e in before.elements), url_changed=before.url != after.url,
                         title_changed=before.title != after.title, text_changed=before.text != after.text)
    old: dict[tuple[Any, ...], list[UIElement]] = {}
    new: dict[tuple[Any, ...], list[UIElement]] = {}
    for element in before.elements:
        old.setdefault(_match_key(element), []).append(element)
    for element in after.elements:
        new.setdefault(_match_key(element), []).append(element)
    updated: list[ElementChange] = []
    for key in old.keys() & new.keys():
        for left, right in zip(old[key], new[key], strict=False):
            fields = tuple(name for name in ("role", "name", "value", "checked", "bounds", "capabilities")
                           if getattr(left, name) != getattr(right, name))
            if fields:
                updated.append(ElementChange(ref=right.ref, fields=fields))
    added = [element.ref for key in new.keys() - old.keys() for element in new[key]]
    removed = [element.ref for key in old.keys() - new.keys() for element in old[key]]
    for key in old.keys() & new.keys():
        removed.extend(element.ref for element in old[key][len(new[key]):])
        added.extend(element.ref for element in new[key][len(old[key]):])
    return StateDiff(
        before_state_id=before.state_id, after_state_id=after.state_id,
        added=tuple(added), removed=tuple(removed),
        updated=tuple(updated), url_changed=before.url != after.url,
        title_changed=before.title != after.title, text_changed=before.text != after.text,
    )


class StateStore:
    """Small immutable observation store with a monotonically increasing root epoch."""

    def __init__(self, capacity: int = 16):
        if capacity < 2:
            raise ValueError("state capacity must be at least 2")
        self.capacity = capacity
        self._states: OrderedDict[str, UIState] = OrderedDict()
        self._epochs: dict[str, int] = {}
        self._latest: dict[str, str] = {}
        self._retired: set[str] = set()

    def add(self, snapshot: Any) -> UIState:
        root_id = _root_id(snapshot)
        epoch = self._epochs.get(root_id, 0) + 1
        state = compile_state(snapshot, epoch=epoch)
        self._epochs[root_id] = epoch
        self._latest[root_id] = state.state_id
        self._states[state.state_id] = state
        self._retired.discard(state.state_id)
        while len(self._states) > self.capacity:
            state_id, removed = self._states.popitem(last=False)
            if self._latest.get(removed.root_id) == state_id:
                self._latest.pop(removed.root_id, None)
        return state

    def get(self, state_id: str) -> UIState | None:
        return self._states.get(state_id)

    def is_latest(self, state: UIState) -> bool:
        return (state.state_id not in self._retired
                and self._latest.get(state.root_id) == state.state_id
                and self._epochs.get(state.root_id) == state.epoch)

    def retire(self, state_id: str) -> None:
        """Make an observation ineligible after any attempted backend action."""
        if state_id in self._states:
            self._retired.add(state_id)

    def states(self) -> Iterable[UIState]:
        return tuple(self._states.values())
