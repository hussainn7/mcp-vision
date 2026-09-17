"""Strong types for the reasoning harness.

The runtime owns reality. These types capture what the model currently believes
about reality plus the machinery to keep sense divided from action. They are
plain dataclasses on purpose: cheap, trivially testable, and easy to serialise
(`dataclasses.asdict`) without coupling reasoning to a schema validator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

# --- Provenance / verification vocabulary ---------------------------------

# Where a belief came from. Keep these coarse; fine "certainty scores" live on Belief.
P_USER_ASSERTED = "user_assertion"
P_INFERRED = "inferred"
P_OBSERVED = "observed"
P_VERIFIED = "verified"

# How confident a verification/assumption is.
VerificationLevel = Literal["none", "partial", "verified"]

# --- Consequence model -----------------------------------------------------
# Autonomy should decrease as these go up (see consequence.py).


class ConsequenceLevel(int, Enum):
    NONE = 0  # read / inspect / search / research
    MINOR = 1  # minor reversible changes (draft, organise a copy)
    RECOVERABLE = 2  # meaningful but recoverable (rename, move within undo)
    SIGNIFICANT = 3  # external comms, submissions, purchases, destructive
    CRITICAL = 4  # irreversible, expensive, identity/security-sensitive

    @property
    def label(self) -> str:
        return {
            self.NONE: "read/inspect/search",
            self.MINOR: "minor reversible",
            self.RECOVERABLE: "recoverable",
            self.SIGNIFICANT: "significant external effect",
            self.CRITICAL: "critical / irreversible",
        }[self]


# --- Failure classification (strategies.py maps these to recovery) ---------


class FailureCategory(str, Enum):
    ACTION = "action_failure"
    OBSERVATION = "observation_failure"
    INTERPRETATION = "interpretation_failure"
    STRATEGY = "strategy_failure"
    ASSUMPTION = "assumption_failure"
    TOOL = "tool_failure"
    PERMISSION = "permission_failure"
    SITE = "site_application_failure"
    KNOWLEDGE = "knowledge_gap"
    AMBIGUITY = "ambiguity"
    ENVIRONMENT = "environment_mismatch"


# --- Beliefs / assumptions / uncertainty ----------------------------------


@dataclass
class Belief:
    """A single belief about the world, with provenance and confidence."""

    value: Any
    source: str = P_OBSERVED
    confidence: float = 0.8
    freshness: float | None = None  # 0..1; None = unaged / unknown
    verification: VerificationLevel = "none"
    note: str = ""


@dataclass
class Assumption:
    """An explicit, revisable working assumption.

    Assumptions are allowed to drive low-risk research freely but must be
    re-verified before the consequence level they gate. They never silently
    dissolve into reasoning.
    """

    statement: str
    basis: str = "inferred"
    confidence: float = 0.5
    reversible: bool = True
    depends_on: list[str] = field(default_factory=list)  # decisions it gates
    verify_before: ConsequenceLevel = ConsequenceLevel.SIGNIFICANT
    source: str = P_INFERRED
    active: bool = True


@dataclass
class Uncertainty:
    """An open question. Resolved late, after cheaper sources are tried."""

    question: str
    resolvable_via: list[str] = field(default_factory=list)
    materially_affects: bool = True
    blocks_consequence: ConsequenceLevel = ConsequenceLevel.SIGNIFICANT
    resolved: bool = False
    answer: Any = None
    asked_user: bool = False


# --- Goals vs. execution tasks ---------------------------------------------
# A goal is a desired outcome; a task is a specific action toward it. A failed
# task must NOT imply a failed goal.


@dataclass
class Goal:
    objective: str
    state: str = "active"  # active | in_progress | satisfied | blocked
    success_conditions: list[str] = field(default_factory=list)
    consequence_level: ConsequenceLevel = ConsequenceLevel.NONE
    subgoals: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class TaskItem:
    """A concrete thing performed to reach a goal. Not a goal itself."""

    action: str
    params: dict = field(default_factory=dict)
    rationale: str = ""
    state: str = "pending"  # pending | running | done | failed


# --- Strategy ---------------------------------------------------------------


@dataclass
class Strategy:
    """The plan of attack, with escape hatches (not a rigid task list)."""

    current: str = ""
    alternatives: list[str] = field(default_factory=list)
    failure_conditions: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=list)
    reason: str = ""

    def switch_to(self, name: str, reason: str = "") -> None:
        if self.current:
            self.history.append(self.current)
        self.current = name
        self.reason = reason


# --- Proposing & expecting an action ---------------------------------------


@dataclass
class ExpectedOutcome:
    """Predict before acting: expected effect plus success/failure signals."""

    action: str = ""
    expected_effect: str = ""
    success_signal: str = ""
    failure_signal: str = ""


@dataclass
class ProposedAction:
    action: str  # e.g. "search", "inspect", "click", "fill", "list", "compare"
    params: dict = field(default_factory=dict)
    expected: ExpectedOutcome | None = None
    consequence: ConsequenceLevel = ConsequenceLevel.NONE
    reversible: bool = True
    rationale: str = ""
    shadow: list[str] = field(default_factory=list)  # semantic side effects


# --- Observation & verification --------------------------------------------


@dataclass
class ObservationResult:
    """What the runtime reported back from acting / observing.

    `ok` is about the interaction; goal completion is judged separately.
    """

    ok: bool = False
    message: str = ""
    executed: bool | None = None  # None = unknown
    state: dict = field(default_factory=dict)  # grounded snapshot summary
    new_information: list[str] = field(default_factory=list)


@dataclass
class Failure:
    category: FailureCategory = FailureCategory.ACTION
    detail: str = ""
    recoverable: bool = True


@dataclass
class Verification:
    """Three distinct levels: did the interaction happen, did the environment
    change as expected, and did the user's goal actually advance."""

    action: bool = False
    state: bool = False
    goal: bool = False
    detail: str = ""

    @property
    def success(self) -> bool:
        return self.action and self.state


# --- Progress & completion -------------------------------------------------


@dataclass
class Progress:
    subgoals_completed: int = 0
    uncertainties_resolved: int = 0
    evidence_collected: int = 0
    options_discovered: int = 0
    non_progress_steps: int = 0
    note: str = ""


@dataclass
class Completion:
    satisfied: bool = False
    blocked: bool = False
    required_outcome_covered: bool = False
    unresolved_uncertainty: list[str] = field(default_factory=list)
    evidence_quality: str = ""
    quality_note: str = ""


# --- Memory separation ----------------------------------------------------


@dataclass
class MemoryBank:
    """Working vs session vs durable preference vs capability knowledge."""

    working: dict = field(default_factory=dict)
    session: dict = field(default_factory=dict)
    durable_preferences: dict = field(default_factory=dict)
    capability: dict = field(default_factory=dict)


# --- The whole agent state -------------------------------------------------


@dataclass
class AgentState:
    """Structured belief about the current task and its progress.

    Compact on purpose: this is what the reasoner reasons over, not a full
    conversation transcript. Goals and tasks are kept apart so a failed action
    never looks like a failed goal.
    """

    raw_request: str = ""
    objective: str = ""
    mode: str = ""  # ask | guide | act | research
    consequence_level: ConsequenceLevel = ConsequenceLevel.NONE

    constraints_explicit: dict = field(default_factory=dict)
    constraints_inferred: dict = field(default_factory=dict)
    preferences: dict = field(default_factory=dict)
    entities: dict = field(default_factory=dict)

    facts: list[Belief] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    uncertainties: list[Uncertainty] = field(default_factory=list)

    goals: list[Goal] = field(default_factory=list)
    tasks: list[TaskItem] = field(default_factory=list)

    strategy: Strategy = field(default_factory=Strategy)
    observations: list[dict] = field(default_factory=list)
    previous_attempts: list[TaskItem] = field(default_factory=list)

    progress: Progress = field(default_factory=Progress)
    completion: Completion = field(default_factory=Completion)
    memory: MemoryBank = field(default_factory=MemoryBank)
    meta_checks: int = 0

    # --- helpers ----------------------------------------------------------

    def belief(self, key: str, default: Any = None) -> Any:
        for f in self.facts:
            if f.note == key or (isinstance(f.value, dict) and key in f.value):
                return f.value.get(key) if isinstance(f.value, dict) else f.value
        return default

    def add_fact(self, note: str, value: Any, **kwargs) -> Belief:
        b = Belief(value=value, note=note, **kwargs)
        self.facts = [f for f in self.facts if f.note != note] + [b]
        return b

    def add_assumption(self, statement: str, **kwargs) -> Assumption:
        a = Assumption(statement=statement, **kwargs)
        self.assumptions.append(a)
        return a

    def active_assumptions(self) -> list[Assumption]:
        return [a for a in self.assumptions if a.active]

    def resolved_uncertainty(self, question: str) -> Uncertainty | None:
        for u in self.uncertainties:
            if u.question == question:
                return u
        return None

    def primary_goal(self) -> Goal | None:
        return self.goals[0] if self.goals else None


@dataclass
class Decision:
    """The model's judgment for one loop pass. Compact and structured.

    `next`/`clarification`/`consider_done` are mutually exclusive exit paths the
    harness enforces. Everything else is state the model asks the harness to adopt.
    """

    strategy: str = ""
    next: ProposedAction | None = None
    clarification: str = ""
    consider_done: bool = False
    done_summary: str = ""
    new_assumptions: list[Assumption] = field(default_factory=list)
    resolved_uncertainties: list[str] = field(default_factory=list)  # questions resolved
    new_facts: list[Belief] = field(default_factory=list)
    meta: str = ""  # terse rationale / meta-reasoning note