#!/usr/bin/env python3
"""Create, run, record, and validate the MVP release evidence bundle."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from config import cfg
from readiness import role_readiness
from mcp_vision.native_permissions import native_permission_snapshot
from mcp_vision.release_gate import (
    COUNT_FIELDS, INPUT_MODES, LATENCY_FIELDS, LIVE_CATEGORIES, artifact,
    automated_result, load, metrics_from_replay, new_report, permission_summary,
    save, set_live_result, validate,
)


DEFAULT_CHECKS = (
    ("full_suite", (sys.executable, "-m", "pytest", "-q"), {}),
    ("browser_integration", (sys.executable, "-m", "pytest", "-q",
     "tests/integration/test_browser_runtime.py", "tests/integration/test_live_browser_cdp.py"),
     {"MCP_VISION_BROWSER_TESTS": "1"}),
    ("package_install_smoke", (sys.executable, "scripts/package_install_smoke.py"), {}),
    ("replay_schema", (sys.executable, "-m", "pytest", "-q",
     "tests/test_trace_viewer.py", "tests/test_session_memory.py"), {}),
    ("hardcode_audit", (sys.executable, "-m", "pytest", "-q", "tests/test_hardcode_audit.py"), {}),
    ("release_report_validation", (sys.executable, "-m", "pytest", "-q",
     "tests/test_release_gate.py"), {}),
)


def report_path(value: str) -> Path:
    return Path(value).resolve()


def init_report(path: Path) -> int:
    report = new_report(
        ROOT,
        permissions=permission_summary(native_permission_snapshot()),
        provider_roles=role_readiness(cfg),
    )
    save(report, path)
    print(path)
    return 0


def run_automated(path: Path) -> int:
    report = load(path) if path.exists() else new_report(
        ROOT, permissions=permission_summary(native_permission_snapshot()),
        provider_roles=role_readiness(cfg))
    results = []
    for name, command, additions in DEFAULT_CHECKS:
        started = time.monotonic()
        environment = os.environ.copy()
        environment.update(additions)
        completed = subprocess.run(command, cwd=ROOT, env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        results.append(automated_result(
            name, command, returncode=completed.returncode,
            duration_ms=(time.monotonic() - started) * 1000,
            output=completed.stdout,
        ))
        print(f"{name}: {results[-1]['status']}")
    report["automated"]["checks"] = results
    save(report, path)
    return 0 if all(item["status"] == "pass" for item in results) else 1


def _pairs(values: list[str], allowed: set[str], cast) -> dict:
    result = {}
    for value in values:
        key, separator, raw = value.partition("=")
        if not separator or key not in allowed:
            raise SystemExit(f"invalid key=value field: {value}")
        result[key] = cast(raw)
    return result


def record_live(args) -> int:
    report = load(args.report)
    root = args.report.parent
    screenshots = [artifact(Path(item), root) for item in args.screenshot]
    replay = artifact(Path(args.replay), root) if args.replay else None
    derived = metrics_from_replay(Path(args.replay)) if args.replay else {
        "counts": {}, "execution_paths": [], "latency_ms": {}}
    counts = derived["counts"] | _pairs(args.count, COUNT_FIELDS, int)
    latency = derived["latency_ms"] | _pairs(args.latency, LATENCY_FIELDS, float)
    paths = sorted(set(derived["execution_paths"] + args.execution_path))
    set_live_result(
        report, args.category, status=args.status, task_id=args.task_id,
        input_mode=args.input_mode,
        latency_ms=latency, counts=counts, execution_paths=paths,
        delivery=args.delivery, semantic=args.semantic,
        screenshots=screenshots, replay_bundle=replay, blocker=args.blocker,
    )
    save(report, args.report)
    return 0


def validate_report(path: Path, complete: bool) -> int:
    report = load(path)
    errors = validate(report, path.parent, require_complete=complete)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(f"valid: {report['summary']['go_no_go']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=report_path,
                        default=ROOT / "outputs" / "release_gate" / "report.json")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init")
    sub.add_parser("run-automated")
    live = sub.add_parser("record-live")
    live.add_argument("category", choices=LIVE_CATEGORIES)
    live.add_argument("--status", required=True, choices=("pass", "fail", "blocked", "not_run"))
    live.add_argument("--task-id")
    live.add_argument("--input-mode", choices=sorted(INPUT_MODES))
    live.add_argument("--latency", action="append", default=[], metavar="NAME=MS")
    live.add_argument("--count", action="append", default=[], metavar="NAME=N")
    live.add_argument("--execution-path", action="append", default=[])
    live.add_argument("--delivery", action="store_true")
    live.add_argument("--semantic", action="store_true")
    live.add_argument("--screenshot", action="append", default=[])
    live.add_argument("--replay")
    live.add_argument("--blocker")
    check = sub.add_parser("validate")
    check.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    if args.action == "init":
        return init_report(args.report)
    if args.action == "run-automated":
        return run_automated(args.report)
    if args.action == "record-live":
        return record_live(args)
    return validate_report(args.report, args.complete)


if __name__ == "__main__":
    raise SystemExit(main())
