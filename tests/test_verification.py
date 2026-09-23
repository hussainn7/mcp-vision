from __future__ import annotations

from mcp_vision.browser import BrowserSnapshot
from mcp_vision.state import compile_state
from mcp_vision.verification import VerificationEngine, VerificationOutcome, VerificationPredicate


def state(state_id="s1", *, text="Saved 2 seconds ago", value="Paris", title="Settings"):
    return compile_state(BrowserSnapshot(
        snapshot_id=state_id, root_id="root", url="https://app.test/settings", title=title, text=text,
        elements=[{"index": 0, "role": "textbox", "name": "City", "value": value,
                   "checked": None, "x": 1, "y": 2, "w": 100, "h": 20,
                   "identity": {"dom": "input:city"}}]), epoch=1 if state_id == "s1" else 2)


def test_verifier_returns_evidence_for_common_predicates():
    engine = VerificationEngine()
    current = state()
    cases = [
        VerificationPredicate(kind="element_exists", role="textbox", name="City"),
        VerificationPredicate(kind="element_missing", role="button", name="Delete"),
        VerificationPredicate(kind="text_contains", expected="Saved"),
        VerificationPredicate(kind="value_equals", role="textbox", name="City", expected="paris"),
        VerificationPredicate(kind="url_matches", expected=r"/settings$"),
        VerificationPredicate(kind="window_exists", expected="Settings"),
        VerificationPredicate(kind="attribute_equals", role="textbox", name="City",
                              attribute="role", expected="textbox"),
    ]
    results = [engine.verify(current, predicate) for predicate in cases]
    assert all(result.passed for result in results)
    assert results[2].evidence["text_excerpt"] == "Saved 2 seconds ago"
    assert all(result.state_id == "s1" for result in results)


def test_verifier_tracks_replaced_identity_and_state_change():
    engine = VerificationEngine()
    before = state(value="Paris")
    after = state("s2", value="SFO")
    value = engine.verify(after, VerificationPredicate(kind="value_equals", target_ref="@e0", expected="SFO"),
                          before=before)
    changed = engine.verify(after, VerificationPredicate(kind="state_changed"), before=before)
    assert value.passed and value.target == "@e0"
    assert changed.passed and changed.evidence["before_state_id"] == "s1"


def test_custom_verifier_must_be_registered_and_returns_evidence():
    engine = VerificationEngine()
    predicate = VerificationPredicate(kind="custom", custom_name="has-city", expected="Paris")
    assert not engine.verify(state(), predicate).passed
    engine.register("has-city", lambda current, _before, pred: (
        current.elements[0].value == pred.expected, current.elements[0].value, {"method": "test"}))
    result = engine.verify(state(), predicate)
    assert result.passed and result.evidence["method"] == "test"


def test_verifier_distinguishes_unsatisfied_from_unknown():
    engine = VerificationEngine()
    current = state()
    absent = engine.verify(current, VerificationPredicate(kind="text_contains", expected="Never present"))
    missing_baseline = engine.verify(current, VerificationPredicate(kind="state_changed"))
    ambiguous = engine.verify(current, VerificationPredicate(kind="value_equals", expected="Paris"))
    assert absent.outcome is VerificationOutcome.UNSATISFIED
    assert missing_baseline.outcome is VerificationOutcome.UNKNOWN
    assert ambiguous.outcome is VerificationOutcome.SATISFIED
    assert missing_baseline.model_dump(mode="json")["passed"] is False


def test_degraded_observation_cannot_prove_negative_evidence():
    snapshot = BrowserSnapshot(
        snapshot_id="degraded", root_id="root", url="", title="Calculator", text="",
        elements=[], source="macos-accessibility",
        identity={"fallback_reasons": ["ax_traversal_error", "ocr_unavailable"]},
    )
    current = compile_state(snapshot, epoch=1)
    result = VerificationEngine().verify(
        current, VerificationPredicate(kind="element_missing", role="button", name="Equals"))
    assert current.quality.degraded
    assert current.quality.reasons == ("ax_traversal_error", "ocr_unavailable")
    assert result.outcome is VerificationOutcome.UNKNOWN
    assert result.evidence["observation_degraded"] is True
