"""Reproducible local browser workflows for measuring planner handoffs and FastPath."""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Any

from mcp_vision.browser import BrowserRuntime
from mcp_vision.fast_policy import FastPolicy, JevPolicy, RulePolicy
from mcp_vision.fastpath import FastPath, FastPathTask
from mcp_vision.state import Operation
from mcp_vision.transactions import TransactionRuntime
from mcp_vision.verification import DEFAULT_VERIFIER, VerificationPredicate


AUTOCOMPLETE = """<!doctype html><html><head><title>Route planner</title></head><body>
<main><h1>Route planner</h1><label for="destination">Destination</label><input id="destination">
<div id="suggestions"></div><p id="result">No destination selected</p></main><script>
const destination = document.querySelector('#destination'), suggestions = document.querySelector('#suggestions');
destination.addEventListener('input', () => setTimeout(() => {
  suggestions.innerHTML = destination.value ? '<button type="button" id="sfo">San Francisco SFO</button>' : '';
  document.querySelector('#sfo')?.addEventListener('click', () => {
    destination.value = 'SFO'; document.querySelector('#result').textContent = 'Destination set to SFO';
    history.pushState({}, '', '/route/sfo'); suggestions.replaceChildren();
  });
}, 30));
</script></body></html>"""

MODAL = """<!doctype html><html><head><title>Settings</title></head><body>
<h1>Account settings</h1><button type="button" id="open">Open preferences</button>
<dialog id="prefs"><h2>Preferences</h2><label><input id="dark" type="checkbox"> Dark mode</label></dialog>
<p id="result">Light mode</p><script>
const prefs = document.querySelector('#prefs'), dark = document.querySelector('#dark');
document.querySelector('#open').addEventListener('click', () => prefs.showModal());
dark.addEventListener('change', () => document.querySelector('#result').textContent = dark.checked ? 'Dark mode enabled' : 'Light mode');
</script></body></html>"""

DRAFT = """<!doctype html><html><head><title>Draft</title></head><body>
<h1>Draft workspace</h1><label for="title">Draft title</label><input id="title">
<button type="button" onclick="result.textContent='Preview ready'">Preview</button>
<form><button>Submit</button></form><p id="result">Nothing prepared</p></body></html>"""


@dataclass(frozen=True)
class ManualStep:
    operation: Operation
    name: str
    argument: str | bool | int | None = None


@dataclass(frozen=True)
class BenchmarkTask:
    name: str
    path: str
    html: str
    subgoal: str
    inputs: dict[str, str | bool | int]
    completion: VerificationPredicate
    manual: tuple[ManualStep, ...]


TASKS = (
    BenchmarkTask(
        name="draft_preview", path="/draft", html=DRAFT,
        subgoal="Fill Draft title and click Preview", inputs={"Draft title": "Fast ideas"},
        completion=VerificationPredicate(kind="text_contains", expected="Preview ready"),
        manual=(ManualStep(Operation.TYPE, "Draft title", "Fast ideas"),
                ManualStep(Operation.PRESS, "Preview")),
    ),
    BenchmarkTask(
        name="autocomplete_spa", path="/route", html=AUTOCOMPLETE,
        subgoal="Set Destination to SFO", inputs={"Destination": "San Francisco"},
        completion=VerificationPredicate(kind="text_contains", expected="Destination set to SFO"),
        manual=(ManualStep(Operation.TYPE, "Destination", "San Francisco"),
                ManualStep(Operation.PRESS, "San Francisco SFO")),
    ),
    BenchmarkTask(
        name="modal_settings", path="/settings", html=MODAL,
        subgoal="Open preferences and enable Dark mode", inputs={"Dark mode": True},
        completion=VerificationPredicate(kind="text_contains", expected="Dark mode enabled"),
        manual=(ManualStep(Operation.PRESS, "Open preferences"),
                ManualStep(Operation.SET_CHECKED, "Dark mode", True)),
    ),
)


def _metrics(strategy: str, task: BenchmarkTask, *, passed: bool, elapsed: float,
             observations: int, actions: int, planner_handoffs: int, retries: int = 0,
             stale: int = 0, wrong_targets: int = 0, verification_failures: int = 0,
             background: int = 0, foreground: int = 0, reason: str = "") -> dict[str, Any]:
    return {
        "strategy": strategy, "task": task.name, "success": passed,
        "duration_ms": round(elapsed * 1000, 2), "planner_handoffs": planner_handoffs,
        "system2_calls": None, "observations": observations, "actions": actions,
        "retries": retries, "stale_rejections": stale, "wrong_target_actions": wrong_targets,
        "verification_failures": verification_failures, "background_actions": background,
        "foreground_actions": foreground, "reason": reason,
    }


async def _manual_run(runtime: BrowserRuntime, task: BenchmarkTask) -> dict[str, Any]:
    """Foundation baseline: a planner must hand off every individual semantic action."""
    started = time.perf_counter()
    tx = TransactionRuntime(runtime)
    initial = state = await tx.observe()
    observations, actions, background, foreground = 1, 0, 0, 0
    reason = ""
    for spec in task.manual:
        matches = []
        for candidate in state.candidates:
            target = state.element(candidate.target_ref or "")
            if candidate.operation is spec.operation and target and target.name == spec.name:
                matches.append(candidate)
        if len(matches) != 1:
            reason = f"Expected one {spec.operation.value} target named {spec.name}; found {len(matches)}."
            break
        candidate = matches[0]
        arguments = {candidate.argument: spec.argument} if candidate.argument else {}
        receipt = await tx.execute(state.state_id, candidate.id, **arguments)
        actions += int(receipt.action.executed is not False)
        background += int(receipt.action.executed is not False and receipt.action.evidence.get("background") is True)
        foreground += int(receipt.action.executed is not False and receipt.action.evidence.get("background") is False)
        if receipt.successor_state is None:
            reason = receipt.action.message
            break
        state = receipt.successor_state
        observations += 1
    verification = DEFAULT_VERIFIER.verify(state, task.completion, before=initial)
    return _metrics("foundation_stepwise", task, passed=verification.passed, elapsed=time.perf_counter() - started,
                    observations=observations, actions=actions, planner_handoffs=len(task.manual),
                    verification_failures=int(not verification.passed), background=background,
                    foreground=foreground, reason=reason or verification.message)


async def _fastpath_run(runtime: BrowserRuntime, task: BenchmarkTask, policy: FastPolicy | None = None,
                        strategy: str = "rules_fastpath") -> dict[str, Any]:
    started = time.perf_counter()
    result = await FastPath(TransactionRuntime(runtime), policy or RulePolicy()).run(FastPathTask(
        subgoal=task.subgoal, inputs=task.inputs, completion=task.completion,
    ))
    return _metrics(
        strategy, task, passed=result.subgoal_complete, elapsed=time.perf_counter() - started,
        observations=result.metrics.observations, actions=result.metrics.actions, planner_handoffs=1,
        retries=result.metrics.retries, stale=result.metrics.stale_rejections,
        verification_failures=int(not result.subgoal_complete),
        background=result.metrics.background_actions, foreground=result.metrics.foreground_actions,
        reason=result.reason,
    ) | {
        "fastpath_calls": len(result.steps),
        "provider_calls": sum(step.provider_call == "successful" for step in result.steps),
        "api_errors": sum(step.provider_call == "failed" for step in result.steps),
        "decisions": [step.model_dump(mode="json", exclude={"transaction"}) for step in result.steps],
    }


async def run_policy_comparison(*, iterations: int = 1, headed: bool = False) -> dict[str, Any]:
    """Run Rules and live Jev on identical local pages; the stepwise arm is not a live System-2 model."""
    if not 1 <= iterations <= 5:
        raise ValueError("iterations must be between 1 and 5")
    from playwright.async_api import async_playwright

    rows: list[dict[str, Any]] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not headed)
        page = await browser.new_page(viewport={"width": 1000, "height": 720})
        html_by_path = {task.path: task.html for task in TASKS}
        await page.route("https://benchmark.mcp-vision.invalid/**", lambda route: route.fulfill(
            body=html_by_path.get("/" + route.request.url.split("/", 3)[-1].split("?", 1)[0], DRAFT),
            content_type="text/html"))
        try:
            for _iteration in range(iterations):
                for task in TASKS:
                    for policy, strategy in ((None, "structured_handoff"),
                                             (RulePolicy(), "rules_fastpath"),
                                             (JevPolicy(), "jev_fastpath")):
                        await page.goto(f"https://benchmark.mcp-vision.invalid{task.path}")
                        runtime = BrowserRuntime(page=page, allow_writes=True,
                                                 allowed_origins=["https://benchmark.mcp-vision.invalid"])
                        if policy is None:
                            row = await _manual_run(runtime, task)
                            row["strategy"] = strategy
                            row.update({"fastpath_calls": 0, "provider_calls": 0, "api_errors": 0,
                                        "decisions": [], "system2_live": False})
                        else:
                            row = await _fastpath_run(runtime, task, policy, strategy)
                            row["system2_live"] = False
                        rows.append(row)
        finally:
            await browser.close()

    summary = {}
    for strategy in ("structured_handoff", "rules_fastpath", "jev_fastpath"):
        selected = [row for row in rows if row["strategy"] == strategy]
        summary[strategy] = {
            "runs": len(selected), "successes": sum(row["success"] for row in selected),
            "median_duration_ms": round(statistics.median(row["duration_ms"] for row in selected), 2),
            "actions": sum(row["actions"] for row in selected),
            "provider_calls": sum(row["provider_calls"] for row in selected),
            "api_errors": sum(row["api_errors"] for row in selected),
            "retries": sum(row["retries"] for row in selected),
            "wrong_target_actions": sum(row["wrong_target_actions"] for row in selected),
            "verification_failures": sum(row["verification_failures"] for row in selected),
        }
    return {"schema": 1, "scope": "identical local Chromium tasks; Jev is live; System-2 model not invoked",
            "iterations": iterations, "tasks": [task.name for task in TASKS],
            "strategies": summary, "runs": rows}


async def run_browser_benchmark(*, iterations: int = 3, headed: bool = False) -> dict[str, Any]:
    """Run both strategies against identical local dynamic pages; no network or model required."""
    if not 1 <= iterations <= 20:
        raise ValueError("iterations must be between 1 and 20")
    from playwright.async_api import async_playwright

    rows: list[dict[str, Any]] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not headed)
        page = await browser.new_page(viewport={"width": 1000, "height": 720})
        html_by_path = {task.path: task.html for task in TASKS}
        await page.route("https://benchmark.mcp-vision.invalid/**", lambda route: route.fulfill(
            body=html_by_path.get("/" + route.request.url.split("/", 3)[-1].split("?", 1)[0], DRAFT),
            content_type="text/html"))
        try:
            for _iteration in range(iterations):
                for task in TASKS:
                    for strategy in (_manual_run, _fastpath_run):
                        await page.goto(f"https://benchmark.mcp-vision.invalid{task.path}")
                        runtime = BrowserRuntime(page=page, allow_writes=True,
                                                 allowed_origins=["https://benchmark.mcp-vision.invalid"])
                        rows.append(await strategy(runtime, task))
        finally:
            await browser.close()

    strategies = {}
    for name in ("foundation_stepwise", "rules_fastpath"):
        selected = [row for row in rows if row["strategy"] == name]
        strategies[name] = {
            "runs": len(selected), "successes": sum(row["success"] for row in selected),
            "success_rate": round(sum(row["success"] for row in selected) / len(selected), 3),
            "median_duration_ms": round(statistics.median(row["duration_ms"] for row in selected), 2),
            "planner_handoffs": sum(row["planner_handoffs"] for row in selected),
            "actions": sum(row["actions"] for row in selected),
            "observations": sum(row["observations"] for row in selected),
            "retries": sum(row["retries"] for row in selected),
            "stale_rejections": sum(row["stale_rejections"] for row in selected),
            "wrong_target_actions": sum(row["wrong_target_actions"] for row in selected),
            "verification_failures": sum(row["verification_failures"] for row in selected),
            "background_actions": sum(row["background_actions"] for row in selected),
            "foreground_actions": sum(row["foreground_actions"] for row in selected),
        }
    return {
        "schema": 1,
        "scope": "local deterministic Chromium; no model latency or external network",
        "iterations": iterations,
        "tasks": [task.name for task in TASKS],
        "strategies": strategies,
        "runs": rows,
    }
