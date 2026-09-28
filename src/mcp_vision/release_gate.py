"""Fail-closed evidence model for public MVP release qualification.

The gate deliberately keeps automated checks and installed-product dogfood in
separate sections.  A mocked or scripted check can never satisfy a live item.
Artifact contents are not copied into the report; only relative paths and
SHA-256 digests are retained.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "mcp-vision.release-gate/v1"
LIVE_CATEGORIES = (
    "calculator_arithmetic",
    "create_and_populate_item",
    "browser_research_navigation",
    "clarification_resume",
    "explain_visible_error",
    "background_action",
)
STATUSES = {"pass", "fail", "blocked", "not_run"}
INPUT_MODES = {"typed", "hotkey_voice", "typed_and_hotkey_voice"}
LATENCY_FIELDS = {
    "input_to_action", "speech_end_to_action", "preparation_lead",
    "action_to_verified", "total",
}
COUNT_FIELDS = {
    "model_calls", "context_bytes", "browser_protocol_calls", "retries",
    "focus_changes",
}
_SAFE_TOKEN = re.compile(r"^[a-zA-Z0-9_.:/+-]{1,160}$")
_FORBIDDEN_KEYS = {
    "prompt", "transcript", "page_text", "screen_text", "email", "phone",
    "clipboard", "raw_output", "model_output", "user_text",
}


class ReleaseGateError(ValueError):
    """A release report is incomplete, unsafe, or internally inconsistent."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_sha(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(path: Path, root: Path) -> dict[str, str]:
    path = path.resolve()
    root = root.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ReleaseGateError("evidence must be inside the release artifact directory") from exc
    if not path.is_file():
        raise ReleaseGateError(f"evidence file is missing: {relative}")
    return {"path": relative.as_posix(), "sha256": sha256_file(path)}


def permission_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep permission state while removing PIDs and user-specific paths."""
    allowed = (
        "platform", "process", "bundleId", "accessibility", "screenRecording",
        "microphone", "microphoneStatus", "speechRecognition",
        "speechRecognitionStatus", "scope",
    )
    return {key: snapshot.get(key) for key in allowed}


def metrics_from_replay(path: Path) -> dict[str, Any]:
    """Derive bounded release counters from a replay without copying content."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("events")
    if not isinstance(events, list):
        raise ReleaseGateError("replay bundle must contain an events list")
    counts = {name: 0 for name in sorted(COUNT_FIELDS)}
    paths: set[str] = set()
    latency: dict[str, float] = {}
    first_ts = last_ts = None
    for event in events:
        if not isinstance(event, dict):
            raise ReleaseGateError("replay events must be objects")
        kind = str(event.get("type", ""))
        if kind == "llm_call":
            counts["model_calls"] += 1
        if kind in {"browser_protocol", "cdp_call"}:
            counts["browser_protocol_calls"] += 1
        counts["browser_protocol_calls"] += max(0, int(event.get("protocol_calls", 0) or 0))
        counts["context_bytes"] += max(0, int(event.get("context_bytes", 0) or 0))
        if kind in {"retry", "replan"}:
            counts["retries"] += 1
        if kind in {"focus_change", "window_focus"}:
            counts["focus_changes"] += 1
        execution_path = event.get("execution_path")
        if isinstance(execution_path, str) and _SAFE_TOKEN.fullmatch(execution_path):
            paths.add(execution_path)
        timestamp = event.get("ts")
        if isinstance(timestamp, (int, float)):
            first_ts = timestamp if first_ts is None else min(first_ts, timestamp)
            last_ts = timestamp if last_ts is None else max(last_ts, timestamp)
        if kind == "run_end" and isinstance(event.get("total_ms"), (int, float)):
            latency["total"] = round(max(0.0, float(event["total_ms"])), 1)
    if "total" not in latency and first_ts is not None and last_ts is not None:
        latency["total"] = round(max(0.0, (last_ts - first_ts) * 1000), 1)
    return {"counts": counts, "execution_paths": sorted(paths), "latency_ms": latency}


def new_report(root: Path, *, permissions: dict | None = None,
               provider_roles: dict | None = None,
               product_path: str = "/Applications/MCP-Vision.app") -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "generated_at": utc_now(),
        "build_sha": git_sha(root),
        "environment": {
            "permissions": permissions or {"status": "not_recorded"},
            "provider_roles": provider_roles or {"status": "not_recorded"},
        },
        "automated": {"checks": []},
        "live": {
            "product_path": product_path,
            "categories": [empty_live_result(name) for name in LIVE_CATEGORIES],
        },
        "summary": {"automated": "not_run", "live": "not_run", "go_no_go": "no-go"},
    }


def report_for_current_build(report: dict | None, root: Path, *,
                             permissions: dict | None = None,
                             provider_roles: dict | None = None,
                             product_path: str = "/Applications/MCP-Vision.app") -> dict[str, Any]:
    """Reuse evidence only while it belongs to the exact current commit."""
    current = git_sha(root)
    if report is not None and report.get("schema") == SCHEMA and report.get("build_sha") == current:
        return report
    return new_report(root, permissions=permissions, provider_roles=provider_roles,
                      product_path=product_path)


def empty_live_result(category: str) -> dict[str, Any]:
    return {
        "category": category,
        "status": "not_run",
        "task_id": None,
        "input_mode": None,
        "latency_ms": {},
        "counts": {name: 0 for name in sorted(COUNT_FIELDS)},
        "execution_paths": [],
        "verification": {"delivery": False, "semantic": False},
        "evidence": {"screenshots": [], "replay_bundle": None},
        "blocker": "not_run",
    }


def automated_result(name: str, command: Iterable[str], *, returncode: int,
                     duration_ms: float, output: bytes) -> dict[str, Any]:
    """Create a bounded, content-free command result.

    Output is represented by byte count and digest. This preserves exact
    reproducibility without placing test output, paths, or personal data in the
    report.
    """
    args = [str(part) for part in command]
    if not name or not all(_SAFE_TOKEN.fullmatch(part) for part in args):
        raise ReleaseGateError("automated check names and command arguments must be safe tokens")
    return {
        "name": name,
        "status": "pass" if returncode == 0 else "fail",
        "command": args,
        "returncode": int(returncode),
        "duration_ms": round(max(0.0, duration_ms), 1),
        "output_bytes": len(output),
        "output_sha256": hashlib.sha256(output).hexdigest(),
    }


def set_live_result(report: dict, category: str, *, status: str,
                    task_id: str | None = None, input_mode: str | None = None,
                    latency_ms: dict[str, float] | None = None,
                    counts: dict[str, int] | None = None,
                    execution_paths: list[str] | None = None,
                    delivery: bool = False, semantic: bool = False,
                    screenshots: list[dict[str, str]] | None = None,
                    replay_bundle: dict[str, str] | None = None,
                    blocker: str | None = None) -> None:
    if category not in LIVE_CATEGORIES:
        raise ReleaseGateError(f"unknown live category: {category}")
    if status not in STATUSES:
        raise ReleaseGateError(f"unknown status: {status}")
    replacement = {
        "category": category,
        "status": status,
        "task_id": task_id,
        "input_mode": input_mode,
        "latency_ms": latency_ms or {},
        "counts": {name: int((counts or {}).get(name, 0)) for name in sorted(COUNT_FIELDS)},
        "execution_paths": execution_paths or [],
        "verification": {"delivery": bool(delivery), "semantic": bool(semantic)},
        "evidence": {"screenshots": screenshots or [], "replay_bundle": replay_bundle},
        "blocker": blocker,
    }
    categories = report["live"]["categories"]
    categories[[item["category"] for item in categories].index(category)] = replacement
    summarize(report)


def _validate_artifact(value: Any, root: Path, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise ReleaseGateError(f"{label} must contain only path and sha256")
    relative = Path(value["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseGateError(f"{label} path must stay inside the artifact directory")
    target = root / relative
    if not target.is_file() or sha256_file(target) != value["sha256"]:
        raise ReleaseGateError(f"{label} is missing or its digest changed")


def _walk_for_private_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_KEYS:
                raise ReleaseGateError(f"personal/raw field is forbidden: {key}")
            _walk_for_private_fields(child)
    elif isinstance(value, list):
        for child in value:
            _walk_for_private_fields(child)


def validate(report: dict, artifact_root: Path, *, require_complete: bool = False) -> list[str]:
    errors: list[str] = []
    try:
        _walk_for_private_fields(report)
        encoded = json.dumps(report, ensure_ascii=True)
        if re.search(r"/(?:Users|home)/[^/\"\\]+", encoded):
            raise ReleaseGateError("user-specific absolute paths are forbidden")
        if report.get("schema") != SCHEMA:
            raise ReleaseGateError("unsupported release report schema")
        if not re.fullmatch(r"[0-9a-f]{40}|unknown", str(report.get("build_sha"))):
            raise ReleaseGateError("build_sha must be a full Git SHA")
        if set(report.get("automated", {})) != {"checks"}:
            raise ReleaseGateError("automated results must remain a separate checks section")
        categories = report.get("live", {}).get("categories", [])
        if [item.get("category") for item in categories] != list(LIVE_CATEGORIES):
            raise ReleaseGateError("live report must contain each category exactly once in canonical order")
        for check in report["automated"]["checks"]:
            if check.get("status") not in {"pass", "fail", "blocked"}:
                raise ReleaseGateError("automated check has invalid status")
            if "output" in check:
                raise ReleaseGateError("raw automated output must not be retained")
        for item in categories:
            status = item.get("status")
            if status not in STATUSES:
                raise ReleaseGateError(f"{item['category']}: invalid status")
            if set(item.get("latency_ms", {})) - LATENCY_FIELDS:
                raise ReleaseGateError(f"{item['category']}: unknown latency field")
            if set(item.get("counts", {})) != COUNT_FIELDS:
                raise ReleaseGateError(f"{item['category']}: incomplete counters")
            if status == "pass":
                if not item.get("task_id") or item.get("input_mode") not in INPUT_MODES:
                    raise ReleaseGateError(f"{item['category']}: passed run lacks task/input identity")
                if not all(item.get("verification", {}).get(key) for key in ("delivery", "semantic")):
                    raise ReleaseGateError(f"{item['category']}: passed state change lacks both verifications")
                evidence = item.get("evidence", {})
                if not evidence.get("screenshots") or not evidence.get("replay_bundle"):
                    raise ReleaseGateError(f"{item['category']}: passed run lacks screenshot or replay evidence")
                for index, shot in enumerate(evidence["screenshots"]):
                    _validate_artifact(shot, artifact_root, f"{item['category']} screenshot {index}")
                _validate_artifact(evidence["replay_bundle"], artifact_root, f"{item['category']} replay")
            elif status in {"fail", "blocked"} and not item.get("blocker"):
                raise ReleaseGateError(f"{item['category']}: failure lacks a reproducible blocker code")
        if require_complete and report.get("summary", {}).get("go_no_go") != "go":
            raise ReleaseGateError("release is not fully qualified")
    except (KeyError, TypeError, ValueError, ReleaseGateError) as exc:
        errors.append(str(exc))
    return errors


def summarize(report: dict) -> dict[str, str]:
    checks = report.get("automated", {}).get("checks", [])
    categories = report.get("live", {}).get("categories", [])
    automated = "pass" if checks and all(item.get("status") == "pass" for item in checks) else (
        "not_run" if not checks else "fail")
    live = "pass" if categories and all(item.get("status") == "pass" for item in categories) else (
        "not_run" if categories and all(item.get("status") == "not_run" for item in categories) else "fail")
    result = {"automated": automated, "live": live,
              "go_no_go": "go" if automated == live == "pass" else "no-go"}
    report["summary"] = result
    return result


def save(report: dict, path: Path) -> None:
    summarize(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
