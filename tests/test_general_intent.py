from datetime import date

import pytest

from reasoning.date_facts import normalize_dates
from reasoning.intent import Operation, SlotSpec, begin_intent
import asyncio
from types import SimpleNamespace

from reasoning.reasoners import BrowserOperationExecutor, Step, structured_result, validate_step


@pytest.mark.parametrize("domain,slot", [("appointment", "clinician"), ("delivery", "address"), ("event", "venue")])
def test_missing_slots_are_domain_independent(domain, slot):
    state = begin_intent(f"Arrange a {domain}", (SlotSpec(slot, f"What {slot}?"),))
    question = state.next_operation()
    assert question["operation"] is Operation.ASK_USER and question["slot"] == slot
    task_id = state.task_id
    resumed = state.answer("the supplied value")
    assert resumed["operation"] is Operation.REPLAN and resumed["task_id"] == task_id


@pytest.mark.parametrize("phrase,start,end", [
    ("today", "2026-09-27", "2026-09-27"),
    ("tomorrow", "2026-09-28", "2026-09-28"),
    ("next week", "2026-09-28", "2026-10-04"),
    ("next month", "2026-10-01", "2026-10-31"),
    ("2026-10-02 to 2026-10-05", "2026-10-02", "2026-10-05"),
])
def test_date_normalization(phrase, start, end):
    result = normalize_dates(phrase, today=date(2026, 9, 27))
    assert result.start.isoformat() == start and result.end.isoformat() == end


def test_general_operation_validation_and_structured_results():
    assert validate_step(Step(Operation.ENTER_TEXT, {"target": "semantic-id", "text": "value"}))
    with pytest.raises(ValueError):
        validate_step(Step(Operation.SELECT, {"value": "x"}))
    result = structured_result(fields={"name": "A"}, sources=["https://a", "https://a"], complete=True)
    assert result == {"complete": True, "fields": {"name": "A"}, "sources": ["https://a"], "missing": []}


def test_autocomplete_sequence_reobserves_before_selecting_dynamic_option():
    class Runtime:
        def __init__(self):
            self.calls, self.poll = [], 0

        async def fill(self, snapshot_id, target, text):
            self.calls.append(("fill", snapshot_id, target, text))

        async def snapshot(self):
            self.poll += 1
            names = [] if self.poll == 1 else [{"name": "San Francisco All airports"}]
            return SimpleNamespace(text="", elements=names, snapshot_id=f"fresh-{self.poll}")

        async def select(self, snapshot_id, target, value):
            self.calls.append(("select", snapshot_id, target, value))

    async def scenario():
        runtime = Runtime()
        executor = BrowserOperationExecutor(runtime, poll_interval=0, max_polls=3)
        await executor.execute(Step(Operation.ENTER_TEXT,
                                    {"snapshot_id": "old", "target": 4, "text": "San Francisco"}))
        fresh = await executor.execute(Step(Operation.WAIT_FOR, {"condition": "San Francisco All airports"}))
        await executor.execute(Step(Operation.SELECT,
                                    {"snapshot_id": fresh.snapshot_id, "target": 8, "value": "SFO"}))
        assert runtime.calls == [("fill", "old", 4, "San Francisco"),
                                 ("select", "fresh-2", 8, "SFO")]

    asyncio.run(scenario())
