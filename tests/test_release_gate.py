import json
from pathlib import Path

import pytest

from mcp_vision.release_gate import (
    LIVE_CATEGORIES, ReleaseGateError, artifact, automated_result,
    metrics_from_replay, new_report, permission_summary, save, set_live_result,
    report_for_current_build, validate,
)


def evidence(tmp_path: Path, name: str):
    path = tmp_path / name
    path.write_bytes(("safe-" + name).encode())
    return artifact(path, tmp_path)


def complete_report(tmp_path: Path):
    report = new_report(tmp_path)
    report["build_sha"] = "a" * 40
    report["automated"]["checks"] = [automated_result(
        "suite", ["python", "-m", "pytest"], returncode=0, duration_ms=12.5, output=b"passed")]
    for index, category in enumerate(LIVE_CATEGORIES):
        set_live_result(
            report, category, status="pass", task_id=f"task-{index}",
            input_mode="hotkey_voice" if index == 0 else "typed",
            latency_ms={"total": 100 + index},
            counts={"model_calls": 1, "context_bytes": 120, "browser_protocol_calls": 0,
                    "retries": 0, "focus_changes": 1},
            execution_paths=["native"], delivery=True, semantic=True,
            screenshots=[evidence(tmp_path, f"{category}.png")],
            replay_bundle=evidence(tmp_path, f"{category}.replay.json"),
        )
    return report


def test_complete_report_is_go_and_round_trips(tmp_path):
    report = complete_report(tmp_path)
    path = tmp_path / "report.json"
    save(report, path)
    loaded = json.loads(path.read_text())
    assert loaded["summary"] == {"automated": "pass", "live": "pass", "go_no_go": "go"}
    assert validate(loaded, tmp_path, require_complete=True) == []


def test_automated_and_live_evidence_cannot_substitute_for_each_other(tmp_path):
    report = new_report(tmp_path)
    report["build_sha"] = "b" * 40
    report["automated"]["checks"] = [automated_result(
        "suite", ["python", "-m", "pytest"], returncode=0, duration_ms=1, output=b"ok")]
    assert report["summary"]["go_no_go"] == "no-go"
    assert validate(report, tmp_path, require_complete=True) == ["release is not fully qualified"]


def test_pass_requires_delivery_semantics_screenshot_and_replay(tmp_path):
    report = complete_report(tmp_path)
    report["live"]["categories"][0]["verification"]["semantic"] = False
    errors = validate(report, tmp_path)
    assert "both verifications" in errors[0]


def test_artifacts_are_contained_and_digest_checked(tmp_path):
    outside = tmp_path.parent / "outside-release-evidence"
    outside.write_text("x")
    with pytest.raises(ReleaseGateError, match="inside"):
        artifact(outside, tmp_path)
    report = complete_report(tmp_path)
    (tmp_path / "calculator_arithmetic.png").write_text("changed")
    assert "digest changed" in validate(report, tmp_path)[0]


def test_private_or_raw_fields_are_rejected(tmp_path):
    report = complete_report(tmp_path)
    report["live"]["categories"][0]["transcript"] = "private words"
    assert "forbidden" in validate(report, tmp_path)[0]


def test_failed_result_needs_reproducible_blocker(tmp_path):
    report = new_report(tmp_path)
    report["build_sha"] = "c" * 40
    set_live_result(report, LIVE_CATEGORIES[0], status="fail")
    assert "blocker code" in validate(report, tmp_path)[0]


def test_replay_metrics_are_derived_without_copying_event_content(tmp_path):
    path = tmp_path / "replay.json"
    path.write_text(json.dumps({"events": [
        {"type": "run_start", "ts": 1.0, "context_bytes": 50},
        {"type": "llm_call", "ts": 1.1, "context_bytes": 70},
        {"type": "transaction", "ts": 1.2, "execution_path": "cdp", "protocol_calls": 4},
        {"type": "focus_change", "ts": 1.3},
        {"type": "retry", "ts": 1.4},
        {"type": "run_end", "ts": 1.5, "total_ms": 500},
    ]}))
    result = metrics_from_replay(path)
    assert result == {
        "counts": {"browser_protocol_calls": 4, "context_bytes": 120,
                   "focus_changes": 1, "model_calls": 1, "retries": 1},
        "execution_paths": ["cdp"], "latency_ms": {"total": 500.0},
    }


def test_permission_summary_removes_process_paths_and_pid():
    safe = permission_summary({
        "pid": 123, "bundlePath": "/Users/person/private", "executablePath": "/private/bin",
        "platform": "darwin", "process": "MCP-Vision", "bundleId": "org.mcpvision.contextual",
        "accessibility": True,
    })
    assert "pid" not in safe and "bundlePath" not in safe and "executablePath" not in safe
    assert safe["accessibility"] is True


def test_commit_change_invalidates_all_previous_evidence(tmp_path):
    previous = complete_report(tmp_path)
    previous["build_sha"] = "f" * 40
    current = report_for_current_build(previous, tmp_path)
    assert current["build_sha"] == "unknown"
    assert current["automated"]["checks"] == []
    assert all(item["status"] == "not_run" for item in current["live"]["categories"])
