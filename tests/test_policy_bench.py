import json
import time

from bench.policy_bench import run_policy_benchmark
from fast_policy import BoundedPolicy, Candidate, rules_decide


CANDIDATES = [
    Candidate("from", "combobox", "From airport", ("fill",)),
    Candidate("to", "combobox", "To airport", ("fill",)),
    Candidate("search", "button", "Search flights", ("click",)),
]


def test_rules_and_jev_target_only_keep_operation_bounded():
    baseline = rules_decide("Type Boston in From airport", CANDIDATES)
    assert (baseline.operation, baseline.target) == ("fill", "from")
    policy = BoundedPolicy(lambda payload: {
        "operation": "click", "target": "from", "confidence": .9,
        "probabilities": [.9, .05, .05], "continuation_id": "jev-next",
    })
    decision = policy.decide("Type Boston in From airport", CANDIDATES,
                             mode="jev_target", continuation_id="jev-prior")
    assert (decision.operation, decision.target) == ("fill", "from")
    assert decision.continuation_id == "jev-next"


def test_jev_operation_and_target_are_validated():
    policy = BoundedPolicy(lambda payload: {
        "operation": "click", "target": "search", "confidence": .92,
        "probabilities": [.92, .04, .04],
    })
    decision = policy.decide("Search now", CANDIDATES, mode="jev_operation_target")
    assert (decision.operation, decision.target) == ("click", "search")
    assert not decision.invalid


def test_malformed_and_timeout_fall_back_without_requiring_jev():
    malformed = BoundedPolicy(lambda payload: {"target": "outside", "confidence": .9})
    result = malformed.decide("Type in From airport", CANDIDATES, mode="jev_target")
    assert result.source.endswith("->rules") and result.invalid and result.escalated

    def slow(payload):
        time.sleep(.05)
        return {"target": "from", "confidence": .9}
    timed = BoundedPolicy(slow, timeout_s=.001).decide(
        "Type in From airport", CANDIDATES, mode="jev_target")
    assert timed.source.endswith("->rules") and timed.escalated

    absent = BoundedPolicy().decide("Type in From airport", CANDIDATES, mode="jev_target")
    assert "configured_unverified" in absent.source


def test_benchmark_report_is_deterministic_and_multi_surface(tmp_path):
    first = run_policy_benchmark()
    second = run_policy_benchmark()
    assert first == second
    assert len(first["surfaces"]) == 5
    assert [row["strategy"] for row in first["rows"]] == [
        "rules", "jev_target", "jev_operation_target", "cheap_model", "strong_model"]
    required = {"accuracy", "latency_ms_mean", "cost_usd_total", "entropy_mean",
                "margin_mean", "invalid_outputs", "escalations"}
    assert all(required <= set(row) for row in first["rows"])
    assert json.loads(json.dumps(first, sort_keys=True)) == first
