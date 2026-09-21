"""State-scoped action transactions with successor observations and evidence."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from mcp_vision.browser import Receipt
from mcp_vision.state import ActionCandidate, Operation, StateDiff, StateStore, UIState, diff_states


class TransactionBackend(Protocol):
    async def snapshot(self): ...
    async def click(self, snapshot_id: str, index: int) -> Receipt: ...
    async def fill(self, snapshot_id: str, index: int, text: str) -> Receipt: ...
    async def select(self, snapshot_id: str, index: int, value: str) -> Receipt: ...
    async def set_checked(self, snapshot_id: str, index: int, checked: bool) -> Receipt: ...
    async def scroll(self, snapshot_id: str, delta_y: int) -> Receipt: ...


class Postcondition(BaseModel):
    kind: Literal["text_contains", "text_absent", "url_equals", "value_equals", "checked_equals"]
    value: str | bool
    target_ref: str | None = None


class PostconditionResult(BaseModel):
    verified: bool
    kind: str
    expected: str | bool
    observed: Any = None
    message: str


class TransactionReceipt(BaseModel):
    transaction_id: str
    action: Receipt
    before_state_id: str
    successor_state: UIState | None = None
    diff: StateDiff | None = None
    postcondition: PostconditionResult | None = None
    status: Literal["verified", "unverified", "blocked", "stale", "error"]
    task_complete: bool = False
    duration_ms: float = 0.0


class TransactionRuntime:
    """Adds immutable observations and one-shot bounded candidates to a backend."""

    def __init__(self, backend: TransactionBackend, *, state_capacity: int = 16):
        self.backend = backend
        self.states = StateStore(state_capacity)
        self._consumed: set[tuple[str, str]] = set()
        self._lock = asyncio.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=200)

    def _event(self, event_type: str, **fields: Any) -> None:
        self._events.append({"ts": round(time.time(), 4), "type": event_type, **fields})

    def record_event(self, event_type: str, **fields: Any) -> None:
        """Append a bounded structured event; intended for orchestration layers."""
        self._event(event_type, **fields)

    def events(self, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        return list(self._events)[-limit:]

    def replay(self, limit: int = 200) -> dict[str, Any]:
        """Return a bounded, serializable replay bundle without screenshots or hidden reasoning."""
        events = self.events(limit)
        state_ids = {str(event[key]) for event in events for key in (
            "state_id", "before_state_id", "after_state_id",
        ) if event.get(key)}
        states = [state.model_dump(mode="json") for state in self.states.states()
                  if state.state_id in state_ids]
        return {"schema": 1, "events": events, "states": states}

    async def observe(self) -> UIState:
        snapshot = await self.backend.snapshot()
        state = self.states.add(snapshot)
        self._event("observation", state_id=state.state_id, root_id=state.root_id,
                    epoch=state.epoch, source=state.source, elements=len(state.elements),
                    candidates=len(state.candidates), content_hash=state.content_hash,
                    url=state.url, title=state.title)
        return state

    async def execute(self, state_id: str, candidate_id: str, *, text: str | None = None,
                      value: str | None = None, checked: bool | None = None,
                      delta_y: int | None = None, milliseconds: int = 100,
                      expect: Postcondition | None = None) -> TransactionReceipt:
        import uuid
        transaction_id = uuid.uuid4().hex
        started = time.perf_counter()
        async with self._lock:
            state = self.states.get(state_id)
            if state is None or not self.states.is_latest(state):
                return self._failure(transaction_id, state_id, "stale", "State is missing, evicted, or superseded.", started)
            candidate = state.candidate(candidate_id)
            if candidate is None or candidate.state_id != state_id:
                return self._failure(transaction_id, state_id, "blocked", "Candidate is not part of this state.", started)
            token = (state_id, candidate_id)
            if token in self._consumed:
                return self._failure(transaction_id, state_id, "stale", "Candidate was already attempted; reobserve.", started)
            try:
                self._validate_arguments(candidate, text=text, value=value, checked=checked, delta_y=delta_y,
                                         milliseconds=milliseconds)
            except (TypeError, ValueError) as exc:
                return self._failure(transaction_id, state_id, "error", str(exc), started)
            self._consumed.add(token)  # consume before dispatch; uncertain input must never be replayed
            self._event("candidate", state_id=state_id, candidate_id=candidate.id,
                        operation=candidate.operation.value, target_ref=candidate.target_ref,
                        risk=candidate.risk.value, selected={
                            "id": candidate.id, "label": candidate.label,
                            "operation": candidate.operation.value, "target_ref": candidate.target_ref,
                            "risk": candidate.risk.value,
                        }, alternatives=[{
                            "id": option.id, "label": option.label,
                            "operation": option.operation.value, "target_ref": option.target_ref,
                            "risk": option.risk.value,
                        } for option in state.candidates if option.id != candidate.id])
            try:
                receipt = await self._dispatch(candidate, state, text=text, value=value, checked=checked,
                                               delta_y=delta_y, milliseconds=milliseconds)
            except (TypeError, ValueError) as exc:
                return self._failure(transaction_id, state_id, "error", str(exc), started)
            if candidate.operation not in {Operation.REPLAN}:
                # Backends consume observations even when policy blocks or dispatch fails.
                self.states.retire(state_id)
            successor = None
            difference = None
            condition = None
            if receipt.executed is not False:
                try:
                    settle = getattr(self.backend, "settle", None)
                    if settle is not None:
                        await settle(candidate.operation.value)
                    successor = await self.observe()
                    difference = diff_states(state, successor)
                    if expect:
                        condition = self._verify(expect, state, successor, candidate)
                except Exception as exc:
                    receipt = receipt.model_copy(update={"status": "error", "executed": receipt.executed,
                                                         "message": f"{receipt.message} Successor observation failed: {type(exc).__name__}."})
            status = receipt.status
            if condition is not None:
                status = "verified" if condition.verified else "unverified"
            elif receipt.executed:
                status = "unverified"  # primitive read-back is not semantic task success
            result = TransactionReceipt(
                transaction_id=transaction_id, action=receipt, before_state_id=state_id,
                successor_state=successor, diff=difference, postcondition=condition, status=status,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            self._event("transaction", transaction_id=transaction_id, state_id=state_id,
                        candidate_id=candidate.id, action=receipt.action, status=status,
                        executed=receipt.executed,
                        execution_path=receipt.evidence.get("execution_path"),
                        background=receipt.evidence.get("background"),
                        message=receipt.message, evidence=receipt.evidence,
                        dur_ms=round(result.duration_ms, 1))
            if difference:
                self._event("state_diff", before_state_id=difference.before_state_id,
                            after_state_id=difference.after_state_id, root_changed=difference.root_changed,
                            added=list(difference.added), removed=list(difference.removed),
                            updated=[change.model_dump(mode="json") for change in difference.updated],
                            url_changed=difference.url_changed, title_changed=difference.title_changed,
                            text_changed=difference.text_changed, changed=difference.changed)
            if condition:
                self._event("postcondition", transaction_id=transaction_id, kind=condition.kind,
                            verified=condition.verified, expected=condition.expected,
                            observed=condition.observed)
            return result

    @staticmethod
    def _failure(transaction_id: str, state_id: str, status: str, message: str, started: float):
        receipt = Receipt(status=status, action="execute_candidate", message=message, executed=False)
        return TransactionReceipt(transaction_id=transaction_id, action=receipt, before_state_id=state_id,
                                  status=status, duration_ms=(time.perf_counter() - started) * 1000)

    @staticmethod
    def _validate_arguments(candidate: ActionCandidate, **arguments):
        if candidate.argument and arguments.get(candidate.argument) is None:
            raise ValueError(f"{candidate.operation.value} requires {candidate.argument}")
        if candidate.operation is Operation.WAIT and not 0 <= arguments["milliseconds"] <= 2000:
            raise ValueError("milliseconds must be between 0 and 2000")

    async def _dispatch(self, candidate: ActionCandidate, state: UIState, **arguments) -> Receipt:
        index = candidate.target_index
        operation = candidate.operation
        if operation is Operation.PRESS:
            return await self.backend.click(state.state_id, index)
        if operation is Operation.TYPE:
            return await self.backend.fill(state.state_id, index, arguments["text"])
        if operation is Operation.SELECT:
            return await self.backend.select(state.state_id, index, arguments["value"])
        if operation is Operation.SET_CHECKED:
            return await self.backend.set_checked(state.state_id, index, arguments["checked"])
        if operation is Operation.SCROLL:
            return await self.backend.scroll(state.state_id, arguments["delta_y"])
        if operation is Operation.WAIT:
            await asyncio.sleep(arguments["milliseconds"] / 1000)
            return Receipt(status="unverified", action="wait", message="Wait completed; successor observed.", executed=True)
        if operation is Operation.REOBSERVE:
            return Receipt(status="unverified", action="reobserve", message="Successor observation requested.", executed=True)
        return Receipt(status="blocked", action=operation.value, message="System-2 replanning requested.", executed=False)

    @staticmethod
    def _verify(expect: Postcondition, before: UIState, after: UIState,
                candidate: ActionCandidate) -> PostconditionResult:
        observed: Any
        if expect.kind == "text_contains":
            observed = str(expect.value) in after.text
            verified = observed
        elif expect.kind == "text_absent":
            observed = str(expect.value) not in after.text
            verified = observed
        elif expect.kind == "url_equals":
            observed = after.url
            verified = observed == expect.value
        else:
            old_target = before.element(expect.target_ref or candidate.target_ref or "")
            matches = [element for element in after.elements if old_target and
                       (element.identity == old_target.identity if any(old_target.identity.model_dump().values())
                        else (element.role, element.name) == (old_target.role, old_target.name))]
            observed = None
            if len(matches) == 1:
                observed = matches[0].value if expect.kind == "value_equals" else matches[0].checked
            verified = observed == expect.value
        return PostconditionResult(verified=bool(verified), kind=expect.kind, expected=expect.value,
                                   observed=observed,
                                   message="Semantic postcondition verified." if verified else "Semantic postcondition was not observed.")
