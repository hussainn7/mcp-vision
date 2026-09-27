"""Domain-neutral intents, slots, and executable operation vocabulary."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Operation(str, Enum):
    OPEN_URL = "OPEN_URL"
    ENTER_TEXT = "ENTER_TEXT"
    SELECT = "SELECT"
    PRESS = "PRESS"
    WAIT_FOR = "WAIT_FOR"
    VERIFY = "VERIFY"
    ASK_USER = "ASK_USER"
    REPLAN = "REPLAN"


@dataclass(frozen=True)
class SlotSpec:
    name: str
    prompt: str
    aliases: tuple[str, ...] = ()
    required: bool = True


@dataclass
class IntentState:
    request: str
    specs: tuple[SlotSpec, ...]
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    slots: dict[str, str] = field(default_factory=dict)
    pending_slot: str | None = None

    def missing(self) -> list[SlotSpec]:
        return [spec for spec in self.specs if spec.required and not self.slots.get(spec.name)]

    def next_operation(self) -> dict[str, Any]:
        missing = self.missing()
        if missing:
            self.pending_slot = missing[0].name
            return {"operation": Operation.ASK_USER, "slot": missing[0].name,
                    "question": missing[0].prompt, "task_id": self.task_id}
        self.pending_slot = None
        return {"operation": Operation.REPLAN, "slots": dict(self.slots), "task_id": self.task_id}

    def answer(self, text: str) -> dict[str, Any]:
        if not self.pending_slot:
            raise RuntimeError("no clarification is pending")
        value = text.strip()
        if not value:
            return self.next_operation()
        self.slots[self.pending_slot] = value
        self.pending_slot = None
        return self.next_operation()


def extract_slots(text: str, specs: tuple[SlotSpec, ...]) -> dict[str, str]:
    """Extract only explicit labelled fields; a reasoner can supply richer candidates."""
    values: dict[str, str] = {}
    for spec in specs:
        labels = (spec.name, *spec.aliases)
        for label in labels:
            match = re.search(rf"(?:^|[,;]\s*){re.escape(label)}\s*(?:is|=|:)\s*([^,;]+)", text, re.I)
            if match:
                values[spec.name] = match.group(1).strip()
                break
    return values


def begin_intent(request: str, specs: tuple[SlotSpec, ...]) -> IntentState:
    state = IntentState(request=request, specs=specs)
    state.slots.update(extract_slots(request, specs))
    return state
