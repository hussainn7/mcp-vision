"""Reusable verified workflows made of semantics and postconditions, never coordinates."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from mcp_vision.core.models import Policy
from mcp_vision.state import ActionCandidate, Operation, UIElement, UIState
from mcp_vision.verification import VerificationPredicate


class SemanticTarget(BaseModel):
    role: str
    name: str = ""
    description: str = ""
    identities: dict[str, str] = Field(default_factory=dict)


class WorkflowAnchor(BaseModel):
    application: str = ""
    origin: str = ""
    title_contains: str = ""
    expected_elements: tuple[SemanticTarget, ...] = ()


class WorkflowStep(BaseModel):
    operation: Operation
    target: SemanticTarget | None = None
    input_key: str | None = None
    risk: Policy
    postcondition: VerificationPredicate
    recovery: tuple[str, ...] = ("reobserve", "semantic_repair", "replan")


class VerifiedWorkflow(BaseModel):
    schema_version: int = 1
    goal: str
    anchor: WorkflowAnchor
    steps: tuple[WorkflowStep, ...]
    completion: VerificationPredicate
    source_status: Literal["verified"] = "verified"


class WorkflowResolution(BaseModel):
    status: Literal["matched", "blocked", "replan"]
    candidate_id: str | None = None
    confidence: float = 0
    semantic_repair: bool = False
    reason: str


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""


def learn_workflow(bundle: dict[str, Any]) -> VerifiedWorkflow | None:
    """Distill only a genuinely verified replay bundle into a value-free playbook."""
    events = bundle.get("events") or []
    end = next((event for event in reversed(events) if event.get("type") == "fastpath_end"), None)
    verified = [event for event in events if event.get("type") == "verification" and event.get("passed")]
    start = next((event for event in events if event.get("type") == "fastpath_start"), None)
    if not end or end.get("status") != "verified" or not verified or not start:
        return None
    states = {state.get("state_id"): state for state in bundle.get("states") or []}
    first = next(iter(states.values()), {})
    completion_data = start.get("completion") or {}
    try:
        completion = VerificationPredicate.model_validate(completion_data)
    except Exception:
        return None
    steps = []
    anchors: list[SemanticTarget] = []
    for event in events:
        if event.get("type") != "candidate":
            continue
        selected = event.get("selected") or {}
        operation = selected.get("operation") or event.get("operation")
        state = states.get(event.get("state_id"), {})
        raw = next((item for item in state.get("elements", [])
                    if item.get("ref") == selected.get("target_ref")), None)
        target = None
        if raw:
            identities = {key: value for key, value in (raw.get("identity") or {}).items() if value}
            target = SemanticTarget(role=str(raw.get("role") or ""), name=str(raw.get("name") or ""),
                                    description=str(raw.get("description") or ""), identities=identities)
            if target not in anchors:
                anchors.append(target)
        try:
            op = Operation(operation)
            risk = Policy(selected.get("risk") or event.get("risk") or Policy.ROUTINE_WRITE)
        except ValueError:
            return None
        steps.append(WorkflowStep(
            operation=op, target=target,
            input_key=target.name if target and op in {Operation.TYPE, Operation.SELECT, Operation.SET_CHECKED} else None,
            risk=risk, postcondition=completion if len(steps) + 1 == len([
                item for item in events if item.get("type") == "candidate"
            ]) else VerificationPredicate(kind="state_changed"),
        ))
    if not steps:
        return None
    return VerifiedWorkflow(
        goal=str(start.get("subgoal") or "")[:500],
        anchor=WorkflowAnchor(origin=_origin(str(first.get("url") or "")),
                              title_contains=str(first.get("title") or "")[:120],
                              expected_elements=tuple(anchors[:8])),
        steps=tuple(steps), completion=completion,
    )


def _target_score(expected: SemanticTarget, element: UIElement) -> tuple[int, bool]:
    live_ids = {key: value for key, value in element.identity.model_dump().items() if value}
    identity = bool(set(expected.identities.items()) & set(live_ids.items()))
    score = 100 if identity else 0
    score += 20 if expected.role == element.role else 0
    score += 35 if expected.name and expected.name.casefold() == element.name.casefold() else 0
    if expected.name and (expected.name.casefold() in element.name.casefold()
                          or element.name.casefold() in expected.name.casefold()):
        score += 10
    return score, not identity and score >= 55


def resolve_step(workflow: VerifiedWorkflow, step_index: int, state: UIState) -> WorkflowResolution:
    """Resolve against the current state; ambiguity or elevated risk always replans/blocks."""
    if not 0 <= step_index < len(workflow.steps):
        return WorkflowResolution(status="replan", reason="Workflow step is out of range.")
    if workflow.anchor.origin and _origin(state.url) != workflow.anchor.origin:
        return WorkflowResolution(status="replan", reason="Current site does not match the workflow anchor.")
    step = workflow.steps[step_index]
    ranked: list[tuple[int, bool, ActionCandidate]] = []
    for candidate in state.candidates:
        if candidate.operation is not step.operation:
            continue
        element = state.element(candidate.target_ref or "")
        if step.target is None:
            ranked.append((1, False, candidate))
        elif element:
            score, repaired = _target_score(step.target, element)
            if score:
                ranked.append((score, repaired, candidate))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
        return WorkflowResolution(status="replan", reason="No unique semantic target matches; reobserve or replan.")
    score, repaired, candidate = ranked[0]
    if candidate.risk is Policy.RESTRICTED_ACTION or (
        step.risk is not Policy.RESTRICTED_ACTION and candidate.risk is Policy.RESTRICTED_ACTION
    ):
        return WorkflowResolution(status="blocked", reason="The current target has elevated safety risk.")
    threshold = 1 if step.target is None else 55
    if score < threshold:
        return WorkflowResolution(status="replan", reason="Semantic match confidence is too low.")
    return WorkflowResolution(status="matched", candidate_id=candidate.id,
                              confidence=min(1, score / 155), semantic_repair=repaired,
                              reason="Exact identity matched." if not repaired else "Semantics repaired a changed identity.")


class WorkflowLibrary:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> list[VerifiedWorkflow]:
        try:
            return [VerifiedWorkflow.model_validate(item) for item in json.loads(self.path.read_text())]
        except (OSError, ValueError, TypeError):
            return []

    def add(self, workflow: VerifiedWorkflow) -> None:
        workflows = [item for item in self.load() if item.goal != workflow.goal]
        workflows.append(workflow)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([item.model_dump(mode="json") for item in workflows[-100:]], indent=2) + "\n")
