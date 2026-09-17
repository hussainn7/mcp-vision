"""Behavioral tests for the reasoning harness loop and its integration adapter."""
from __future__ import annotations

import asyncio

from mcp_vision.reasoning.harness import ReasoningHarness
from mcp_vision.reasoning.reasoners import HeuristicReasoner
from mcp_vision.reasoning.runtime import BackendExecutor
from mcp_vision.reasoning.schemas import (
    ConsequenceLevel,
    ExpectedOutcome,
    ObservationResult,
    ProposedAction,
)
from mcp_vision.task_policy import TaskConstraints


class FakeExecutor:
    """Read-only grounded executor: every action adds evidence."""

    def __init__(self, *, fail_times=0, informative=True):
        self.fail_times = fail_times
        self.informative = informative
        self.n = 0
        self.actions = []

    async def act(self, proposed):
        self.n += 1
        self.actions.append(proposed.action)
        if self.n <= self.fail_times:
            return ObservationResult(ok=False, executed=False,
                                     message="permission denied: not allowed")
        info = [f"evidence {self.n}"] if self.informative else []
        return ObservationResult(ok=True, executed=True, message="ok",
                                 new_information=info)

    async def observe(self):
        return {"text": "results shown", "url": "http://x/search",
                "options": ["a", "b", "c"]}

    async def close(self):
        pass


async def _run(reasoner, request, executor=None, gate=ConsequenceLevel.NONE, **kw):
    h = ReasoningHarness(reasoner, executor=executor, gate=gate, **kw)
    result = await h.run(request)
    return h, result


def test_flights_research_assumes_instead_of_asking():
    h, result = asyncio.run(_run(HeuristicReasoner(), "find flights to sf",
                                 executor=FakeExecutor()))
    assert result["state"] == "done"
    assert "search" in h.actions_taken
    assert any("dates flexible" in a.statement for a in h.state.assumptions)
    assert h.state.progress.evidence_collected >= 1


def test_consequential_goal_pauses_before_acting():
    h, result = asyncio.run(_run(HeuristicReasoner(), "buy the cheapest monitor you can find",
                                 executor=FakeExecutor(), gate=ConsequenceLevel.NONE))
    assert result["state"] == "paused"
    assert "buy" not in h.actions_taken          # never committed
    assert h.actions_not_taken                   # stopped before doing work on it
    assert h.state.completion.satisfied is False


def test_clarification_precedes_consequential_commit_when_precondition_missing():
    # HeuristicReasoner asks one precondition before a consequential action.
    h, result = asyncio.run(_run(HeuristicReasoner(), "send the deck to bob",
                                 executor=FakeExecutor(), gate=ConsequenceLevel.SIGNIFICANT))
    assert result["state"] == "ask"
    assert "who" in result["answer"].lower() or "send" in result["answer"].lower()


def test_failure_is_classified_and_not_a_goal_failure():
    ex = FakeExecutor(fail_times=2)
    h, result = asyncio.run(_run(HeuristicReasoner(), "find flights to sf",
                                 executor=ex))
    assert h.failures  # some actions failed...
    assert all(f.category.value for f in h.failures)
    # ...but the goal still reached a best-effort result, not a goal-failure stop.
    assert result["state"] in {"done", "partial"}


def test_strategy_tracking_switches_on_reasoner_advice():
    results = iter([
        ProposedAction(action="search",
                       expected=ExpectedOutcome(action="search", success_signal="results")),
        ProposedAction(action="search",
                       expected=ExpectedOutcome(action="search", success_signal="results")),
    ])

    def make(strategy):
        if strategy is None:
            return type("D", (), {"strategy": "", "next": None, "clarification": "",
                                  "consider_done": True, "done_summary": "done",
                                  "new_assumptions": [], "resolved_uncertainties": [],
                                  "new_facts": [], "meta": ""})()
        return type("D", (), {"strategy": strategy, "next": next(results),
                              "clarification": "", "consider_done": False,
                              "done_summary": "", "new_assumptions": [],
                              "resolved_uncertainties": [], "new_facts": [], "meta": ""})()

    class Reasoner:
        def __init__(self):
            self.n = 0

        def decide(self, state, meta):
            self.n += 1
            if self.n == 1:
                return make("aggregator")
            if self.n == 2:
                return make("airline-site")
            return make(None)

    h, _result = asyncio.run(_run(Reasoner(), "find flights to sf", executor=FakeExecutor()))
    assert h.state.strategy.current == "airline-site"
    assert h.state.strategy.history == ["aggregator"]


def test_user_update_preserves_evidence_and_updates_constraints():
    h = ReasoningHarness(HeuristicReasoner())
    h.start("find a monitor around 300")
    first_objective = h.state.objective
    obs = h.state.add_fact("options seen", ["A", "B"])
    h.update("actually keep it under 200")
    assert h.state.objective != first_objective or h.state.constraints_explicit
    assert obs in h.state.facts  # still-valid evidence preserved


# --- integration adapter ------------------------------------------------------


class _Snapshot:
    def __init__(self, elements):
        self.snapshot_id = "s1"
        self.url = "https://form.test/"
        self.title = "Form"
        self.text = "Form"
        self.elements = elements
        self.identity = {"status": "anonymous"}


class _Receipt:
    def __init__(self, status="verified", executed=True, message="ok"):
        self.status = status
        self.executed = executed
        self.message = message


class _Backend:
    def __init__(self):
        self.value = ""
        self.elements = [
            dict(index=0, role="textbox", name="Name", value=self.value, x=0, y=0, w=120, h=25),
            dict(index=1, role="button", name="Submit", x=0, y=40, w=80, h=30),
        ]

    async def snapshot(self):
        self.elements[0]["value"] = self.value
        return _Snapshot(list(self.elements))

    async def fill(self, sid, index, value):
        self.value = value
        return _Receipt()

    async def scroll(self, sid, dy):
        return _Receipt()

    async def close(self):
        pass


def test_backend_executor_dispatches_fill_under_constraints():
    backend = _Backend()
    ex = BackendExecutor(backend, constraints=TaskConstraints())

    async def go():
        r = await ex.act(ProposedAction(action="fill",
                                        params={"name": "Name", "role": "textbox",
                                                "value": "Jane"},
                                        consequence=ConsequenceLevel.RECOVERABLE))
        obs = await ex.observe()
        return r, obs

    r, obs = asyncio.run(go())
    assert r.ok and r.executed is True
    assert backend.value == "Jane"


def test_backend_executor_respects_no_submit_constraint():
    backend = _Backend()
    ex = BackendExecutor(backend, constraints=TaskConstraints(no_submit=True))

    async def go():
        # final-submission click is reserved for the user
        return await ex.act(ProposedAction(action="click",
                                           params={"name": "Submit", "role": "button"},
                                           consequence=ConsequenceLevel.RECOVERABLE))

    result = asyncio.run(go())
    assert result.ok is False
    assert "reserved" in result.message or "forbidden" in result.message.lower()