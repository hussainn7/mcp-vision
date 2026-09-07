"""A disposable browser exercise: no model, account, or external requests."""
import json
from mcp_vision.browser import BrowserRuntime

HTML = '''<!doctype html><html><head><title>MCP-Vision sandbox</title></head>
<body><h1>Your model. Observable actions.</h1>
<label for="draft">Draft title</label><input id="draft">
<button onclick="document.querySelector('#status').textContent='Preview ready'">Preview</button>
<p id="status">Nothing submitted</p></body></html>'''


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
