"""Unit tests for the reasoning schemas and policies."""
from __future__ import annotations

from mcp_vision.reasoning.budget import Budget, measure_progress, should_continue
from mcp_vision.reasoning.consequence import (
    autonomy_allowed,
    level_for_action,
    level_for_request,
    shadow_consequences,
)
from mcp_vision.reasoning.intent import analyze, objective_of
from mcp_vision.reasoning.memory import promote_to_durable, reinforce
from mcp_vision.reasoning.reasoners import HeuristicReasoner, parse_decision
from mcp_vision.reasoning.report import compact_state, final_report
from mcp_vision.reasoning.schemas import (
    AgentState,
    ConsequenceLevel,
    ExpectedOutcome,
    ProposedAction,
    P_VERIFIED,
)
from mcp_vision.reasoning.strategies import classify, recovery_for
from mcp_vision.reasoning.uncertainty import (
    Resolver,
    Resolution,
    resolve_unknown,
)
from mcp_vision.reasoning.verify import verify_action, verify_state


def test_level_for_request_and_action():
    assert level_for_request("find flights to sf") is ConsequenceLevel.NONE
    assert level_for_request("buy a laptop") is ConsequenceLevel.SIGNIFICANT
    assert level_for_request("clean up these files") is ConsequenceLevel.RECOVERABLE
    assert level_for_action("search") is ConsequenceLevel.NONE
    assert level_for_action("send") is ConsequenceLevel.SIGNIFICANT
    assert autonomy_allowed(ConsequenceLevel.NONE, gate=ConsequenceLevel.NONE)
    assert not autonomy_allowed(ConsequenceLevel.SIGNIFICANT, gate=ConsequenceLevel.NONE)


def test_shadow_consequences():
    p = ProposedAction(action="submit", params={"label": "Apply"})
    shadows = shadow_consequences(p, "apply for a job")
    assert any("send/submit" in s for s in shadows)


def test_objective_and_intent():
    assert "flights" in objective_of("find flights to sf")
    s = analyze("find something cheap to sf soon")
    assert s.objective
    assert s.preferences.get("cost") == "low"
    assert "sf" in s.raw_request.lower() or s.objective


def test_assumptions_and_verify_before():
    s = AgentState(raw_request="find flights", objective="find flights")
    a = s.add_assumption("dates flexible", reversible=True,
                         verify_before=ConsequenceLevel.SIGNIFICANT)
    assert a.reversible and a in s.active_assumptions()


def test_uncertainty_resolution_order_prefers_assume_over_ask():
    u = _uncertainty()
    state = AgentState()
    gate = ConsequenceLevel.NONE
    res = resolve_unknown(state, u, resolver=Resolver(), gate=gate)
    assert res.how in {"assume", "ask"}


def test_uncertainty_resolved_from_context_before_asking():
    u = _uncertainty()
    state = AgentState()
    res = resolve_unknown(state, u, resolver=Resolver(context_getter=lambda: "ATL"), gate=ConsequenceLevel.NONE)
    assert res.how == "context" and res.value == "ATL"


def test_uncertainty_asks_when_consequential_depends_on_it():
    u = _uncertainty()
    u.blocks_consequence = ConsequenceLevel.NONE  # anything gates it
    state = AgentState()
    state.consequence_level = ConsequenceLevel.SIGNIFICANT
    res = resolve_unknown(state, u, resolver=Resolver(), gate=ConsequenceLevel.SIGNIFICANT)
    assert res.how == "ask"


def _uncertainty():
    from mcp_vision.reasoning.schemas import Uncertainty
    return Uncertainty(question="which airport?", materially_affects=True)


def test_three_level_verification_stays_distinct():
    receipt = type("R", (), {"ok": True, "executed": True, "message": "", "new_information": []})()
    assert verify_action(receipt) is True
    exp = ExpectedOutcome(action="search", success_signal="results")
    assert verify_state(exp, {"text": "here are results"}) == ("success", "results")
    assert verify_state(exp, {"text": "nothing yet"})[0] in {"unexpected", "failure"}


def test_failure_classification_and_recovery():
    assert classify("Permission denied").value == "permission_failure"
    assert classify("permission denied: not allowed").value == "permission_failure"
    assert recovery_for(classify("permission denied"))  # non-empty recovery advice


def test_progress_and_budget_track_results_not_clicks():
    state = AgentState()
    measure_progress(state, gained_info=True, subgoal_done=True,
                     option_found=False, uncertainty_resolved=False)
    assert state.progress.non_progress_steps == 0
    keep, _ = should_continue(state, budget=Budget(max_actions=5), action_counter=1)
    assert keep is True


def test_budget_does_not_treat_steps_as_impossible():
    # Many steps producing progress should not halt just because N was reached.
    state = AgentState()
    for _ in range(60):
        measure_progress(state, gained_info=True, subgoal_done=False,
                         option_found=False, uncertainty_resolved=False)
    keep, why = should_continue(state, budget=Budget(max_actions=40, exceed_policy="rethink"),
                                action_counter=60)
    assert keep is True  # progress wins over the cap


def test_memory_does_not_promote_weak_task_assumptions_to_durable():
    state = AgentState()
    f = state.add_fact("prefers economy", "economy", source=P_VERIFIED, confidence=0.5)
    assert promote_to_durable(state, "prefers economy") is False  # low confidence


def test_parse_decision_roundtrip():
    d = parse_decision('{"strategy":"compare","next":{"action":"search","params":{"q":"x"},'
                       '"success_signal":"results","consequence":0},"consider_done":true}')
    assert d.strategy == "compare" and d.next.action == "search" and d.consider_done


def test_compact_and_final_report_are_terse():
    state = analyze("find flights to sf")
    HeuristicReasoner()._first_action(state, state.objective)  # drills the flight assumption in
    c = compact_state(state)
    assert {"objective", "known", "assumptions", "strategy", "progress"} <= set(c)
    r = final_report(state, actions_taken=["search"], actions_not_taken=[])
    assert "outcome" in r and "assumptions" in r