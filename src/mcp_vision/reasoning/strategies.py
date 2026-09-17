"""Strategy management and failure classification -> recovery.

A goal is not dead because one action or one strategy failed. Classify the
failure first, then let the failure type drive the recovery move. Do not blindly
retry the same approach.
"""
from __future__ import annotations

from mcp_vision.reasoning.schemas import AgentState, Failure, FailureCategory

# What kind of recovery each failure category maps to. The model can override;
# this is the deterministic default so the system always degrades gracefully.
RECOVERY: dict[FailureCategory, str] = {
    FailureCategory.ACTION: "try another grounded interaction mechanism",
    FailureCategory.OBSERVATION: "re-observe then verify against a fresh expectation",
    FailureCategory.INTERPRETATION: "revise the reading of the evidence, then re-test",
    FailureCategory.STRATEGY: "abandon this strategy; pick an alternative",
    FailureCategory.ASSUMPTION: "revise the working assumption and its gated decisions",
    FailureCategory.TOOL: "swap tools / source",
    FailureCategory.PERMISSION: "surface the blocker; do not retry silently",
    FailureCategory.SITE: "switch sources (another site / aggregator)",
    FailureCategory.KNOWLEDGE: "research the gap before deciding",
    FailureCategory.AMBIGUITY: "reduce ambiguity via context, then confirm if still needed",
    FailureCategory.ENVIRONMENT: "match environment (re-invoke on the intended surface)",
}


def classify(detail: str, *, receipt_ok: bool | None = None,
             expected_signal_seen: bool | None = None) -> FailureCategory:
    """First-pass failure classification. Cheap and deterministic.

    `receipt_ok=None` means the runtime could not tell us. Stronger domain
    classifiers can have the model refine this later.
    """
    low = (detail or "").lower()
    if any(tok in low for tok in ("deny", "permission", "not allowed", "blocked", "unauthorized", "sign in")):
        return FailureCategory.PERMISSION
    if any(tok in low for tok in ("timeout", "not connected", "site", "page error", "503", "captcha", "network")):
        return FailureCategory.SITE
    if any(tok in low for tok in ("no target", "ambiguous", "not sure which", "unclear")):
        return FailureCategory.AMBIGUITY
    if receipt_ok is False:
        return FailureCategory.ACTION
    if expected_signal_seen is False:
        return FailureCategory.OBSERVATION
    return FailureCategory.ACTION


def recovery_for(category: FailureCategory) -> str:
    return RECOVERY.get(category, "reassess then continue")


def consider_strategy_switch(state: AgentState, *, stall_threshold: int = 3) -> str | None:
    """Return a recommendation to switch strategy when progress has stalled and
    there are alternatives left. Returns None if staying put is fine."""
    if state.strategy.alternatives and state.progress.non_progress_steps >= stall_threshold:
        return state.strategy.alternatives[0]
    return None