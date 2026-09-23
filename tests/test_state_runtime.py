from __future__ import annotations

import asyncio

from mcp_vision.browser import BrowserSnapshot, Receipt
from mcp_vision.fast_policy import JevPolicy, MockPolicy, RulePolicy, jev_status
from mcp_vision.state import Operation, StateStore, compile_state, diff_states
from mcp_vision.transactions import Postcondition, TransactionRuntime
from mcp_vision.verification import StabilityPolicy


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
        self.snapshots = [snapshot("s1"), snapshot("s2", text="Saved", value="Paris"),
                          snapshot("s3", text="Saved", value="Paris")]
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
    assert state.quality.sources == ("dom-accessibility",)
    assert not state.quality.degraded


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
            expect=Postcondition(
                kind="text_contains", value="Saved",
                stability=StabilityPolicy(consecutive_samples=2, cadence_ms=1, timeout_ms=10),
            ),
        )
        assert receipt.status == "verified"
        assert receipt.action.status == "verified"  # primitive read-back retained separately
        assert receipt.successor_state.state_id == "s3"
        assert receipt.diff.changed and receipt.postcondition.verified and receipt.postcondition.stable
        assert receipt.postcondition.samples == 2
        assert receipt.task_complete is False
        assert backend.calls == [("fill", "s1", 0, "Paris")]
        assert [event["type"] for event in runtime.events()] == [
            "observation", "candidate", "observation", "transaction", "state_diff", "postcondition",
        ]
        replay = runtime.replay()
        assert replay["schema"] == 1 and len(replay["states"]) == 2
        choice = next(event for event in replay["events"] if event["type"] == "candidate")
        assert choice["selected"]["operation"] == "type"
        assert any(item["operation"] == "press" for item in choice["alternatives"])
        change = next(event for event in replay["events"] if event["type"] == "state_diff")
        assert change["updated"] == [{"ref": "@e0", "fields": ["value"]}]
        repeated = await runtime.execute(state.state_id, fill.id, text="Paris")
        assert repeated.status == "stale" and repeated.action.executed is False
    asyncio.run(run())


def test_transaction_delivers_mutation_once_while_waiting_for_stability():
    async def run():
        backend = Backend()
        backend.snapshots = [snapshot("s1"), snapshot("s2"),
                             snapshot("s3", text="Saved", value="Paris"),
                             snapshot("s4", text="Saved", value="Paris")]
        runtime = TransactionRuntime(backend)
        state = await runtime.observe()
        fill = next(item for item in state.candidates if item.operation is Operation.TYPE)
        result = await runtime.execute(state.state_id, fill.id, text="Paris")
        assert result.status == "verified" and result.postcondition.samples == 3
        assert result.postcondition.predicate == "value_equals"
        assert backend.calls == [("fill", "s1", 0, "Paris")]

    asyncio.run(run())


def test_fast_policies_only_return_supplied_safe_candidates(monkeypatch):
    async def run():
        state = compile_state(snapshot("s1"), epoch=1)
        rule = await RulePolicy().choose("press Continue", state)
        assert rule.candidate_id and not rule.needs_system2
        mock = await MockPolicy(rule.candidate_id).choose("anything", state)
        assert mock.candidate_id == rule.candidate_id
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        jev = await JevPolicy(load_env=False).choose("press Continue", state)
        assert jev.candidate_id is None and jev.needs_system2
        assert jev.provider_call == "not_attempted" and jev.fallback == "system2"
    asyncio.run(run())


def test_button_submit_risk_requires_real_form_association():
    loose = snapshot("s1")
    loose.elements[1]["input_type"] = "submit"
    loose.elements[1]["submits"] = False
    safe = compile_state(loose, epoch=1)
    press = next(item for item in safe.candidates if item.operation is Operation.PRESS)
    assert not press.requires_confirmation

    loose.elements[1]["submits"] = True
    restricted = compile_state(loose, epoch=2)
    press = next(item for item in restricted.candidates if item.operation is Operation.PRESS)
    assert press.requires_confirmation


def test_jev_parallel_heads_use_only_selected_operation_target(monkeypatch):
    async def run():
        state = compile_state(snapshot("s1"), epoch=1)
        press = next(item for item in state.candidates if item.operation is Operation.PRESS)
        operations = {item.operation.value for item in state.candidates}
        remaining = (1 - 0.7) / (len(operations) - 1)
        operation_probabilities = {operation: remaining for operation in operations}
        operation_probabilities["press"] = 0.7
        response = {"answers": {
            "operation": {"choice": "press", "confidence": 0.91,
                          "probabilities": operation_probabilities},
            "press_target": {"choice": press.id, "confidence": 0.88,
                             "probabilities": {press.id: 1.0}},
            # An invalid unused head must not invalidate the selected press head.
            "type_target": {"choice": "invented", "probabilities": {"invented": 1.0}},
            "progress": {"choice": "25"},
            "needs_system2": {"choice": "no", "confidence": 0.9,
                              "probabilities": {"no": 0.9, "yes": 0.1}},
            "stale": {"choice": "no", "confidence": 0.95,
                      "probabilities": {"no": 0.95, "yes": 0.05}},
            "expected_success": {"choice": "yes", "confidence": 0.8,
                                 "probabilities": {"no": 0.2, "yes": 0.8}},
        }}

        async def fake_to_thread(_fn):
            return response

        monkeypatch.setattr("mcp_vision.fast_policy.asyncio.to_thread", fake_to_thread)
        decision = await JevPolicy(api_key="test").choose("press Continue", state)
        assert decision.candidate_id == press.id and decision.operation == "press"
        assert decision.confidence == 0.88 and decision.progress == 0.25
        assert decision.stale_likelihood == 0.05 and decision.expected_success == 0.8
        assert decision.provider_call == "successful" and decision.fallback is None

    asyncio.run(run())


def test_jev_failures_are_explicit_and_never_select_an_action(monkeypatch):
    async def run():
        state = compile_state(snapshot("s1"), epoch=1)

        async def timeout(_fn):
            raise TimeoutError("secret-bearing provider detail must not escape")

        monkeypatch.setattr("mcp_vision.fast_policy.asyncio.to_thread", timeout)
        failed = await JevPolicy(api_key="test", load_env=False).choose("press Continue", state)
        assert failed.candidate_id is None and failed.needs_system2
        assert failed.provider_call == "failed" and failed.fallback == "system2"
        assert failed.reason == "Jev unavailable or invalid: TimeoutError"

        async def invalid(_fn):
            return {"answers": {"operation": {"choice": "invented", "probabilities": {"invented": 1.0}}}}

        monkeypatch.setattr("mcp_vision.fast_policy.asyncio.to_thread", invalid)
        rejected = await JevPolicy(api_key="test", load_env=False).choose("press Continue", state)
        assert rejected.candidate_id is None and rejected.provider_call == "failed"

    asyncio.run(run())


def test_jev_system2_head_escalates_without_execution(monkeypatch):
    async def run():
        state = compile_state(snapshot("s1"), epoch=1)
        press = next(item for item in state.candidates if item.operation is Operation.PRESS)
        operations = {item.operation.value for item in state.candidates}
        operation_probs = {name: 0.0 for name in operations}
        operation_probs["press"] = 1.0
        response = {"answers": {
            "operation": {"choice": "press", "confidence": 1.0, "probabilities": operation_probs},
            "press_target": {"choice": press.id, "confidence": 1.0, "probabilities": {press.id: 1.0}},
            "needs_system2": {"choice": "yes", "confidence": 0.9,
                              "probabilities": {"yes": 0.9, "no": 0.1}},
        }}

        async def fake(_fn):
            return response

        monkeypatch.setattr("mcp_vision.fast_policy.asyncio.to_thread", fake)
        decision = await JevPolicy(api_key="test", load_env=False).choose("press Continue", state)
        assert decision.candidate_id is None and decision.needs_system2
        assert decision.provider_call == "successful" and decision.fallback == "system2"

    asyncio.run(run())


def test_jev_status_loads_repo_style_dotenv_without_exposing_key(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("TYPESAFE_API_KEY=test-secret\nTYPESAFE_MODEL=jev-test\n")
    monkeypatch.chdir(tmp_path)
    assert jev_status() == {"configured": True, "verified": False, "model": "jev-test",
                            "status": "configured_unverified"}
