"""Durable, provider-neutral checkpoints for contextual tasks."""
from __future__ import annotations

import time
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class ObservationEpoch(BaseModel):
    model_config = ConfigDict(frozen=True)

    state_id: str
    root_id: str
    epoch: int = Field(ge=1)
    content_hash: str = ""


class VerifiedEffect(BaseModel):
    model_config = ConfigDict(frozen=True)

    effect_id: str = Field(default_factory=lambda: uuid4().hex)
    operation: str
    target: str = ""
    value: Any = None
    transaction_id: str = ""
    state_id: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class TaskMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    observations: int = 0
    planner_calls: int = 0
    transactions: int = 0
    verified_effects: int = 0
    questions: int = 0
    resumes: int = 0
    cancellations: int = 0
    started_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def increment(self, field: str, amount: int = 1) -> TaskMetrics:
        if field not in {
            "observations", "planner_calls", "transactions", "verified_effects",
            "questions", "resumes", "cancellations",
        }:
            raise ValueError(f"Unknown task metric: {field}")
        return self.model_copy(update={field: getattr(self, field) + amount, "updated_at": time.time()})


class TaskState(BaseModel):
    """Serializable task state; all planner-defined slots remain opaque to core."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default_factory=lambda: uuid4().hex)
    goal: str
    status: TaskStatus = TaskStatus.PENDING
    supplied_slots: dict[str, Any] = Field(default_factory=dict)
    missing_slots: tuple[str, ...] = ()
    pending_question: str | None = None
    subgoals: tuple[str, ...] = ()
    plan_cursor: int = 0
    completed_verified_effects: tuple[VerifiedEffect, ...] = ()
    observation_ids: tuple[str, ...] = ()
    observations: tuple[ObservationEpoch, ...] = ()
    cancellation_token: str = Field(default_factory=lambda: uuid4().hex)
    provider_continuation: dict[str, Any] | None = None
    replay_events: tuple[dict[str, Any], ...] = ()
    metrics: TaskMetrics = Field(default_factory=TaskMetrics)

    @classmethod
    def create(cls, goal: str, *, supplied_slots: dict[str, Any] | None = None,
               missing_slots: tuple[str, ...] = ()) -> TaskState:
        return cls(goal=goal, supplied_slots=dict(supplied_slots or {}),
                   missing_slots=tuple(dict.fromkeys(missing_slots)))

    def start(self) -> TaskState:
        if self.status is TaskStatus.CANCELLED:
            raise ValueError("Cancelled tasks cannot be restarted.")
        return self.model_copy(update={"status": TaskStatus.RUNNING})

    def record_subgoal(self, subgoal: str) -> TaskState:
        text = " ".join(subgoal.split())[:500]
        if not text or (self.subgoals and self.subgoals[-1] == text):
            return self
        return self.model_copy(update={"subgoals": (*self.subgoals, text)})

    def record_observation(self, state: Any) -> TaskState:
        checkpoint = ObservationEpoch(
            state_id=str(state.state_id), root_id=str(state.root_id), epoch=int(state.epoch),
            content_hash=str(getattr(state, "content_hash", "")),
        )
        observations = (*self.observations, checkpoint)[-32:]
        ids = (*self.observation_ids, checkpoint.state_id)[-64:]
        return self.model_copy(update={
            "observations": observations,
            "observation_ids": ids,
            "metrics": self.metrics.increment("observations"),
        })

    def record_effect(self, effect: VerifiedEffect) -> TaskState:
        if any(item.effect_id == effect.effect_id for item in self.completed_verified_effects):
            return self
        return self.model_copy(update={
            "completed_verified_effects": (*self.completed_verified_effects, effect),
            "plan_cursor": self.plan_cursor + 1,
            "metrics": self.metrics.increment("verified_effects"),
        })

    def record_transaction(self) -> TaskState:
        return self.model_copy(update={"metrics": self.metrics.increment("transactions")})

    def record_planner_call(self) -> TaskState:
        return self.model_copy(update={"metrics": self.metrics.increment("planner_calls")})

    def checkpoint_question(self, question: str, *, missing_slots: tuple[str, ...] = (),
                            provider_continuation: dict[str, Any] | None = None) -> TaskState:
        slots = tuple(dict.fromkeys((*self.missing_slots, *missing_slots)))
        return self.model_copy(update={
            "status": TaskStatus.WAITING,
            "pending_question": question,
            "missing_slots": slots,
            "provider_continuation": provider_continuation or self.provider_continuation,
            "metrics": self.metrics.increment("questions"),
        })

    def merge_reply(self, reply: str, *, supplied_slots: dict[str, Any] | None = None,
                    provider_continuation: dict[str, Any] | None = None) -> TaskState:
        if self.status is TaskStatus.CANCELLED:
            raise ValueError("Cancelled tasks cannot be resumed.")
        supplied = dict(self.supplied_slots)
        supplied.update(supplied_slots or {})
        remaining = list(self.missing_slots)
        if not supplied_slots and len(remaining) == 1:
            supplied[remaining.pop()] = reply
        else:
            remaining = [slot for slot in remaining if slot not in supplied]
        continuation = dict(self.provider_continuation or {})
        continuation.update(provider_continuation or {})
        continuation["latest_reply"] = reply
        return self.model_copy(update={
            "status": TaskStatus.RUNNING,
            "supplied_slots": supplied,
            "missing_slots": tuple(remaining),
            "pending_question": None,
            "provider_continuation": continuation,
            "metrics": self.metrics.increment("resumes"),
        })

    def record_replay(self, events: list[dict[str, Any]]) -> TaskState:
        return self.model_copy(update={"replay_events": tuple((*self.replay_events, *events)[-200:])})

    def validate_resume_epoch(self, state: Any) -> None:
        if not self.observations:
            return
        checkpoint = self.observations[-1]
        if str(state.root_id) != checkpoint.root_id:
            raise ValueError("The task root changed while waiting for input.")
        if int(state.epoch) <= checkpoint.epoch:
            raise ValueError("The resumed observation epoch is stale.")

    def cancel(self) -> TaskState:
        return self.model_copy(update={
            "status": TaskStatus.CANCELLED,
            "pending_question": None,
            "metrics": self.metrics.increment("cancellations"),
        })

    def complete(self) -> TaskState:
        return self.model_copy(update={"status": TaskStatus.COMPLETED, "pending_question": None})

    def fail(self) -> TaskState:
        return self.model_copy(update={"status": TaskStatus.ERROR})
