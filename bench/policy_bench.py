"""Deterministic evidence table for bounded operation/target policies."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fast_policy import Candidate, rules_decide

DEFAULT_CASES = Path(__file__).with_name("policy_cases.json")
STRATEGIES = ("rules", "jev_target", "jev_operation_target", "cheap_model", "strong_model")


def _row(name, decisions, expected):
    total = len(expected)
    correct = sum(d[0] == e["operation"] and d[1] == e["target"]
                  for d, e in zip(decisions, expected))
    invalid = sum(not d[0] or not d[1] for d in decisions)
    escalations = sum((not d[0] or not d[1]) or d[2] < .7 or d[6] < .2 for d in decisions)
    return {
        "strategy": name, "cases": total, "accuracy": round(correct / total, 4) if total else 0,
        "latency_ms_mean": round(sum(d[3] for d in decisions) / total, 3) if total else 0,
        "cost_usd_total": round(sum(d[4] for d in decisions), 6),
        "entropy_mean": round(sum(d[5] for d in decisions) / total, 4) if total else 0,
        "margin_mean": round(sum(d[6] for d in decisions) / total, 4) if total else 0,
        "invalid_outputs": invalid, "escalations": escalations,
    }


def run_policy_benchmark(path=DEFAULT_CASES) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases, predictions = data["cases"], data["frozen_predictions"]
    expected = [case["expected"] for case in cases]
    rows = []
    rule_decisions = []
    for case in cases:
        candidates = [Candidate(item["id"], item["role"], item["name"], tuple(item["operations"]))
                      for item in case["candidates"]]
        decision = rules_decide(case["request"], candidates)
        rule_decisions.append([decision.operation, decision.target, decision.confidence,
                               0.05, 0.0, decision.entropy, decision.margin])
    rows.append(_row("rules", rule_decisions, expected))
    for strategy in STRATEGIES[1:]:
        rows.append(_row(strategy, [predictions[strategy][case["id"]] for case in cases], expected))
    return {"schema_version": 1, "mode": "frozen_replay", "case_set": str(Path(path).name),
            "surfaces": sorted({case["surface"] for case in cases}), "rows": rows}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = run_policy_benchmark(args.cases)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return report


if __name__ == "__main__":
    main()
