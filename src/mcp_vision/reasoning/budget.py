"""Progress detection and a smart budget in place of rigid step caps.

'N steps reached' is not 'task impossible'. A handful of steps producing
progress -> keep going. Several steps with no progress -> rethink. Repeated
failure -> reconsider strategy or assumptions. We separate resource budgets
(steps/actions) from reasoning correctness entirely.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from mcp_vision.reasoning.schemas import AgentState, Progress


@dataclass
class Budget:
    """Resource envelope. Not a correctness limit."""

    max_actions: int = 40
    stall_threshold: int = 3  # consecutive non-progress steps before rethink
    exceed_policy: str = "rethink"  # rethink | escalate | stop

    def progress(self) -> int:
        return self.max_actions

    def __post_init__(self) -> None:
        self.max_actions = max(1, self.max_actions)
        self.stall_threshold = max(1, self.stall_threshold)


def measure_progress(state: AgentState, *, gained_info: bool, subgoal_done: bool,
                     option_found: bool, uncertainty_resolved: bool) -> Progress:
    """Update progress from one observed effect. Reading the goal, not clicks."""
    p = state.progress
    p.subgoals_completed += int(subgoal_done)
    p.uncertainties_resolved += int(uncertainty_resolved)
    p.evidence_collected += int(gained_info)
    p.options_discovered += int(option_found)
    if subgoal_done or gained_info or uncertainty_resolved:
        p.non_progress_steps = 0
        p.note = "forward motion"
    else:
        p.non_progress_steps += 1
        p.note = f"no material progress ({p.non_progress_steps})"
    return p


def should_continue(state: AgentState, *, budget: Budget,
                    action_counter: int) -> tuple[bool, str]:
    """Decide whether to keep looping, and why. Escape hatches stay open."""
    p = state.progress
    if state.completion.satisfied:
        return (False, "objective satisfied; stop")
    if state.completion.blocked:
        return (False, "genuine hard block; return best result")
    if p.non_progress_steps >= budget.stall_threshold and not state.strategy.alternatives:
        # Stalled with nowhere else to go: don't spin, but a block is not a fail.
        if budget.exceed_policy == "stop":
            return (False, "stalled with no alternatives; handing back to user")
    if action_counter >= budget.max_actions:
        if budget.exceed_policy == "stop":
            return (False, "action budget reached with no satisfied objective; returning best result")
        if budget.exceed_policy == "rethink":
            return (True, "budget near; keep going while it produces progress")
    return (True, "continue")