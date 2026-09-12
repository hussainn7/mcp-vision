"""A disposable browser exercise: no model, account, or external requests."""
import json
import base64
import time
from mcp_vision.browser import BrowserRuntime

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
    """Run a fixed exercise through the real runtime, including refusal paths."""
    started = time.monotonic()
    runtime = BrowserRuntime(allow_writes=True, allowed_origins=["https://demo.mcp-vision.invalid"])
    try:
        await runtime._ensure()
        await runtime.page.set_viewport_size({"width": 1000, "height": 720})
        await runtime.page.route("https://demo.mcp-vision.invalid/**",
                                 lambda route: route.fulfill(body=STUDIO_HTML, content_type="text/html"))
        receipts = [await runtime.navigate("https://demo.mcp-vision.invalid/")]
        snap = await runtime.snapshot()
        index = next(e["index"] for e in snap.elements if e["role"] == "textbox")
        receipts.append(await runtime.fill(snap.snapshot_id, index, title))
        receipts.append(await runtime.fill(snap.snapshot_id, index, "Stale overwrite"))
        receipts.append(await runtime.act("click", "Preview", "button"))
        receipts.append(await runtime.verify_text("Preview ready"))
        receipts.append(await runtime.act("click", "Submit", "button"))
        retained = await runtime.page.input_value("#draft") == title
        unsubmitted = await runtime.page.locator("#status").inner_text() == "Preview ready"
        expected = ["verified", "verified", "stale", "unverified", "verified", "blocked"]
        return {"demo_passed": [r.status for r in receipts] == expected and retained and unsubmitted,
                "title": title, "duration_ms": round((time.monotonic() - started) * 1000),
                "receipts": [r.model_dump() for r in receipts],
                "checks": {"draft_retained": retained, "form_unsubmitted": unsubmitted},
                "screenshot": base64.b64encode(await runtime.screenshot()).decode()}
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
