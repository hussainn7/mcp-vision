from __future__ import annotations

import asyncio

from mcp_vision.browser import BrowserSnapshot, Receipt
from mcp_vision.fast_policy import JevPolicy, MockPolicy, RulePolicy
from mcp_vision.state import Operation, StateStore, compile_state, diff_states
from mcp_vision.transactions import Postcondition, TransactionRuntime


def snapshot(state_id: str, *, text: str = "Waiting", value: str = "", title: str = "Fixture"):
    return BrowserSnapshot(
        snapshot_id=state_id, url="https://fixture.test/", title=title, text=text,
        elements=[
            {"index": 0, "role": "textbox", "name": "City", "value": value,
             "x": 10, "y": 20, "w": 200, "h": 30, "tag": "input", "input_type": "text",
             "identity": {"dom": "input:city"}},
            {"index": 1, "role": "button", "name": "Continue", "value": "",
             "x": 10, "y": 60, "w": 100, "h": 30, "tag": "button", "input_type": "button",
             "identity": {"dom": "button:continue"}},
        ],
    )


class Backend:
    def __init__(self):
        self.snapshots = [snapshot("s1"), snapshot("s2", text="Saved", value="Paris")]
        self.calls = []

    async def snapshot(self):
        return self.snapshots.pop(0)

    async def fill(self, state_id, index, text):
        self.calls.append(("fill", state_id, index, text))
        return Receipt(status="verified", action="fill", message="read back", executed=True,
                       evidence={"value_matches": True})

    async def click(self, state_id, index):
        self.calls.append(("click", state_id, index))
        return Receipt(status="unverified", action="click", message="dispatched", executed=True)

    async def select(self, *args):
        raise AssertionError("unexpected")

    async def set_checked(self, *args):
        raise AssertionError("unexpected")

    async def scroll(self, *args):
        raise AssertionError("unexpected")


def test_state_compiles_semantic_refs_capabilities_and_bounded_actions():
    state = compile_state(snapshot("s1"), epoch=7)
    assert state.state_id == "s1" and state.epoch == 7
    assert state.elements[0].ref == "@e0"
    assert state.elements[0].capabilities == (Operation.TYPE,)
    assert all(candidate.state_id == state.state_id for candidate in state.candidates)
    assert {candidate.operation for candidate in state.candidates} >= {
        Operation.TYPE, Operation.PRESS, Operation.SCROLL, Operation.WAIT,
        Operation.REOBSERVE, Operation.REPLAN,
    }


def test_store_epochs_make_old_observation_stale_and_diff_tracks_updates():
    store = StateStore()
    before = store.add(snapshot("s1"))
    after = store.add(snapshot("s2", text="Saved", value="Paris"))
    assert not store.is_latest(before) and store.is_latest(after)
    change = diff_states(before, after)
    assert change.changed and change.text_changed
    assert change.updated[0].fields == ("value",)


def test_transaction_observes_successor_and_requires_semantic_postcondition_for_verified():
    async def run():
        backend = Backend()
        runtime = TransactionRuntime(backend)
        state = await runtime.observe()
        fill = next(item for item in state.candidates if item.operation is Operation.TYPE)
        receipt = await runtime.execute(
            state.state_id, fill.id, text="Paris",
            expect=Postcondition(kind="text_contains", value="Saved"),
        )
        assert receipt.status == "verified"
        assert receipt.action.status == "verified"  # primitive read-back retained separately
        assert receipt.successor_state.state_id == "s2"
        assert receipt.diff.changed and receipt.postcondition.verified
        assert receipt.task_complete is False
        assert backend.calls == [("fill", "s1", 0, "Paris")]
        assert [event["type"] for event in runtime.events()] == [
            "observation", "candidate", "observation", "transaction", "state_diff", "postcondition",
        ]
        repeated = await runtime.execute(state.state_id, fill.id, text="Paris")
        assert repeated.status == "stale" and repeated.action.executed is False
    asyncio.run(run())


def test_fast_policies_only_return_supplied_safe_candidates(monkeypatch):
    async def run():
        state = compile_state(snapshot("s1"), epoch=1)
        rule = await RulePolicy().choose("press Continue", state)
        assert rule.candidate_id and not rule.needs_system2
        mock = await MockPolicy(rule.candidate_id).choose("anything", state)
        assert mock.candidate_id == rule.candidate_id
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        jev = await JevPolicy().choose("press Continue", state)
        assert jev.candidate_id is None and jev.needs_system2
    asyncio.run(run())
