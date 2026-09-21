"""A disposable browser exercise: no model, account, or external requests."""
import json
import base64
import time
from mcp_vision.browser import BrowserRuntime
from mcp_vision.fast_policy import RulePolicy
from mcp_vision.fastpath import FastPath, FastPathTask
from mcp_vision.transactions import TransactionRuntime
from mcp_vision.verification import VerificationPredicate

HTML = '''<!doctype html><html><head><title>MCP-Vision sandbox</title></head>
<body><h1>Your model. Observable actions.</h1>
<label for="draft">Draft title</label><input id="draft">
<button onclick="document.querySelector('#status').textContent='Preview ready'">Preview</button>
<p id="status">Nothing submitted</p></body></html>'''

STUDIO_HTML = '''<!doctype html><html lang="en"><head><title>Draft sandbox</title>
<style>body{margin:0;background:#f3f4ef;color:#243b33;font:17px system-ui;display:grid;place-items:center;min-height:100vh}
main{width:500px;background:white;padding:48px;border:1px solid #d9e2d9;border-radius:20px}
small{color:#517967;letter-spacing:2px}h1{font-size:36px;letter-spacing:-1.5px;margin:18px 0 12px}
p{color:#6c7972;line-height:1.6}label{display:block;margin:28px 0 8px;font-size:14px}
input{box-sizing:border-box;width:100%;padding:14px;border:1px solid #cdd9d0;border-radius:8px;font:inherit}
button{padding:12px 20px;border:0;border-radius:8px;background:#244f40;color:white;font:inherit;margin:20px 8px 0 0}
form{display:inline}form button{background:#edf1eb;color:#647269}#status{padding:18px;background:#eaf5e8;border-radius:8px}
</style></head><body><main><small>MCP-VISION / DISPOSABLE WORKSPACE</small>
<h1>A draft, with proof.</h1><p>Your agent prepares the details. You keep the final say.</p>
<label for="draft">Draft title</label><input id="draft">
<button type="button" onclick="document.querySelector('#status').textContent='Preview ready'">Preview</button>
<form onsubmit="event.preventDefault();document.querySelector('#status').textContent='Submitted'">
<button>Submit</button></form><p id="status">Nothing submitted</p></main></body></html>'''


async def studio_demo(title: str) -> dict:
    """Run the real bounded loop and expose its state/action/verification evidence."""
    started = time.monotonic()
    runtime = BrowserRuntime(allow_writes=True, allowed_origins=["https://demo.mcp-vision.invalid"])
    try:
        await runtime._ensure()
        await runtime.page.set_viewport_size({"width": 1000, "height": 720})
        await runtime.page.route("https://demo.mcp-vision.invalid/**",
                                 lambda route: route.fulfill(body=STUDIO_HTML, content_type="text/html"))
        navigation = await runtime.navigate("https://demo.mcp-vision.invalid/")
        initial = await runtime.snapshot()
        before_screenshot = base64.b64encode(await runtime.screenshot()).decode()
        index = next(e["index"] for e in initial.elements if e["role"] == "textbox")
        transactions = TransactionRuntime(runtime)
        task = FastPathTask(
            subgoal="Fill Draft title and click Preview",
            inputs={"Draft title": title},
            completion=VerificationPredicate(kind="text_contains", expected="Preview ready"),
        )
        fastpath = await FastPath(transactions, RulePolicy()).run(task)
        # Exercise two independent safety invariants after routine execution.
        stale = await runtime.fill(initial.snapshot_id, index, "Stale overwrite")
        blocked = await runtime.act("click", "Submit", "button")
        action_receipts = [step.transaction.action for step in fastpath.steps if step.transaction]
        receipts = [navigation, *action_receipts, stale, blocked]
        retained = await runtime.page.input_value("#draft") == title
        unsubmitted = await runtime.page.locator("#status").inner_text() == "Preview ready"
        states = {state.state_id: state for state in transactions.states.states()}
        steps = []
        for step in fastpath.steps:
            state = states.get(step.state_id)
            target = state.element(step.target_ref or "") if state else None
            transaction = step.transaction
            difference = transaction.diff if transaction else None
            steps.append({
                "number": step.number,
                "state_id": step.state_id,
                "candidate_id": step.candidate_id,
                "operation": step.operation,
                "target_ref": step.target_ref,
                "target": ({"role": target.role, "name": target.name,
                            "bounds": target.bounds.model_dump()} if target else None),
                "policy": step.policy,
                "confidence": step.confidence,
                "decision_ms": round(step.decision_ms, 2),
                "status": step.status,
                "reason": step.reason,
                "execution_path": (transaction.action.evidence.get("execution_path")
                                   if transaction else None),
                "background": (transaction.action.evidence.get("background")
                               if transaction else None),
                "duration_ms": round(transaction.duration_ms, 2) if transaction else None,
                "diff": ({**difference.model_dump(mode="json"), "changed": difference.changed}
                         if difference else None),
            })
        expected = ["verified", "verified", "unverified", "stale", "blocked"]
        passed = ([r.status for r in receipts] == expected and retained and unsubmitted
                  and fastpath.subgoal_complete)
        return {"demo_passed": passed,
                "title": title, "duration_ms": round((time.monotonic() - started) * 1000),
                "receipts": [r.model_dump() for r in receipts],
                "checks": {"draft_retained": retained, "form_unsubmitted": unsubmitted},
                "screenshot": base64.b64encode(await runtime.screenshot()).decode(),
                "before_screenshot": before_screenshot,
                "session": {
                    "goal": "Prepare a draft preview without submitting it",
                    "subgoal": task.subgoal,
                    "status": fastpath.status.value,
                    "reason": fastpath.reason,
                    "policy": "rules",
                    "steps": steps,
                    "metrics": fastpath.metrics.model_dump(mode="json"),
                    "verification": (fastpath.verification.model_dump(mode="json")
                                     if fastpath.verification else None),
                    "events": transactions.events(100),
                    "guardrails": [
                        {"name": "Stale observation rejected", "passed": stale.status == "stale"},
                        {"name": "Submission blocked", "passed": blocked.status == "blocked"},
                    ],
                }}
    finally:
        await runtime.close()


async def run_demo():
    runtime = BrowserRuntime(allow_writes=True, allowed_origins=["https://demo.mcp-vision.invalid"])
    try:
        await runtime._ensure()
        await runtime.page.route("https://demo.mcp-vision.invalid/**",
                                 lambda route: route.fulfill(body=HTML, content_type="text/html"))
        receipts = [await runtime.navigate("https://demo.mcp-vision.invalid/")]
        snap = await runtime.snapshot()
        index = next(e["index"] for e in snap.elements if e["role"] == "textbox")
        receipts.append(await runtime.fill(snap.snapshot_id, index, "My first grounded action"))
        snap = await runtime.snapshot()
        index = next(e["index"] for e in snap.elements if e["name"] == "Preview")
        receipts.append(await runtime.click(snap.snapshot_id, index))
        receipts.append(await runtime.verify_text("Preview ready"))
        ok = [r.status for r in receipts] == ["verified", "verified", "unverified", "verified"]
        print(json.dumps({"demo_passed": ok, "receipts": [r.model_dump() for r in receipts]}, indent=2))
        return ok
    finally:
        await runtime.close()
