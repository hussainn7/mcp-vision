"""Compact state summaries for prompts and for the final user-facing report.

Token-conscious: we persist structured state and feed only what the reasoner
needs, instead of restating history every loop.
"""
from __future__ import annotations

from mcp_vision.reasoning.schemas import AgentState


def compact_state(state: AgentState) -> dict:
    """Small, flat summary the reasoner reasons over each loop pass."""
    return {
        "request": state.raw_request,
        "objective": state.objective,
        "mode": state.mode,
        "known": {f.note: f.value for f in state.facts},
        "assumptions": [a.statement for a in state.active_assumptions()],
        "uncertainties": [u.question for u in state.uncertainties if not u.resolved],
        "strategy": state.strategy.current or "none",
        "alternatives": state.strategy.alternatives,
        "progress": {
            "observations": len(state.observations),
            "non_progress_steps": state.progress.non_progress_steps,
            "evidence": state.progress.evidence_collected,
        },
        "consequence": state.consequence_level.label,
        "recent": state.observations[-3:],
    }


def final_report(state: AgentState, *, actions_taken: list[str],
                 actions_not_taken: list[str]) -> dict:
    """Final task state: outcome, evidence, assumptions, caveats.

    We expose only what materially affects the result, never raw internal essays.
    """
    unresolved = [u.question for u in state.uncertainties if not u.resolved
                  and u.materially_affects]
    assumptions = [a.statement for a in state.active_assumptions() if a.reversible]
    return {
        "outcome": "done" if state.completion.satisfied else (
            "blocked" if state.completion.blocked else "partial"),
        "objective": state.objective,
        "summary": state.completion.quality_note or state.progress.note,
        "evidence": [o.get("text") or o.get("summary") for o in state.observations[-8:]],
        "assumptions": assumptions,
        "caveats": unresolved,
        "actions_taken": actions_taken,
        "actions_not_taken": actions_not_taken,
        "confidence": round(sum(f.confidence for f in state.facts) / max(1, len(state.facts)), 2),
    }