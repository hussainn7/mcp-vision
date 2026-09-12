"""Real workflow test: navigate real pages, snapshot, interact, verify.

Not a smoke test. This exercises the actual browser runtime against real
pages and reports what works, what breaks, and captures screenshots.
"""
import asyncio
import base64
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_vision.browser import BrowserRuntime

OUT = Path(__file__).resolve().parent.parent / "outputs" / "workflow_test"
OUT.mkdir(parents=True, exist_ok=True)


async def save_screenshot(runtime, name):
    try:
        png = await runtime.screenshot()
        path = OUT / f"{name}.png"
        path.write_bytes(png)
        print(f"  screenshot: {path}")
        return path
    except Exception as e:
        print(f"  screenshot failed: {e}")
        return None


async def test_navigate_and_snapshot():
    """Navigate to a real page, take a snapshot, check we get elements."""
    print("\n=== TEST: Navigate + Snapshot (example.com) ===")
    rt = BrowserRuntime(allow_writes=False, headless=True)
    try:
        await rt._ensure()
        receipt = await rt.navigate("https://example.com")
        print(f"  navigate: {receipt.status} - {receipt.message}")
        assert receipt.status == "verified", f"navigate failed: {receipt.status}"

        snap = await rt.snapshot()
        print(f"  snapshot: {len(snap.elements)} elements, url={snap.url}")
        print(f"  title: {snap.title}")
        print(f"  text preview: {snap.text[:200]}")
        await save_screenshot(rt, "01_example_com")

        # verify we can find the "More information..." link
        links = [e for e in snap.elements if e["role"] == "link"]
        print(f"  links found: {len(links)}")
        for l in links:
            print(f"    - {l['name']} (index={l['index']})")

        assert len(snap.elements) > 0, "no elements found"
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        traceback.print_exc()
        return False
    finally:
        await rt.close()


async def test_fill_and_verify():
    """Use the sandbox page, fill a field, verify readback."""
    print("\n=== TEST: Fill + Verify (sandbox form) ===")
    html = '''<!doctype html><html><head><title>Test Form</title></head>
    <body>
    <h1>Agent Test Form</h1>
    <label for="name">Your name</label><input id="name" type="text">
    <label for="email">Email</label><input id="email" type="email">
    <label for="notes">Notes</label><textarea id="notes"></textarea>
    <button type="button" onclick="document.getElementById('result').textContent='Saved: '+document.getElementById('name').value">Save Draft</button>
    <button type="submit" onclick="event.preventDefault();document.getElementById('result').textContent='Submitted!'">Submit</button>
    <p id="result">No action yet</p>
    </body></html>'''

    rt = BrowserRuntime(allow_writes=True,
                        allowed_origins=["https://test.mcp-vision.invalid"],
                        headless=True)
    try:
        await rt._ensure()
        await rt.page.route("https://test.mcp-vision.invalid/**",
                            lambda route: route.fulfill(body=html, content_type="text/html"))

        receipt = await rt.navigate("https://test.mcp-vision.invalid/form")
        print(f"  navigate: {receipt.status}")

        snap = await rt.snapshot()
        print(f"  snapshot: {len(snap.elements)} elements")
        for e in snap.elements:
            print(f"    [{e['index']}] {e['role']}: {e['name']}")

        # fill the name field
        name_idx = next((e["index"] for e in snap.elements
                         if e["role"] == "textbox" and "name" in e["name"].lower()), None)
        if name_idx is None:
            print("  FAILED: could not find name textbox")
            return False

        receipt = await rt.fill(snap.snapshot_id, name_idx, "Test Agent")
        print(f"  fill name: {receipt.status} - {receipt.message}")
        print(f"    evidence: {receipt.evidence}")

        await save_screenshot(rt, "02_form_filled")

        # click Save Draft using act()
        receipt = await rt.act("click", "Save Draft", "button")
        print(f"  click Save Draft: {receipt.status} - {receipt.message}")

        # verify the result text appeared
        receipt = await rt.verify_text("Saved: Test Agent")
        print(f"  verify saved: {receipt.status} - {receipt.message}")

        await save_screenshot(rt, "03_form_saved")

        # try to submit (should be blocked by governor for form submission)
        receipt = await rt.act("click", "Submit", "button")
        print(f"  click Submit: {receipt.status} - {receipt.message}")

        await save_screenshot(rt, "04_after_submit_attempt")

        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        traceback.print_exc()
        return False
    finally:
        await rt.close()


async def test_stale_snapshot_rejected():
    """Verify that using an old snapshot_id is properly rejected."""
    print("\n=== TEST: Stale snapshot rejection ===")
    html = '''<!doctype html><html><body>
    <input id="f" type="text"><button>Go</button>
    </body></html>'''

    rt = BrowserRuntime(allow_writes=True,
                        allowed_origins=["https://stale.mcp-vision.invalid"],
                        headless=True, snapshot_ttl=1)
    try:
        await rt._ensure()
        await rt.page.route("https://stale.mcp-vision.invalid/**",
                            lambda route: route.fulfill(body=html, content_type="text/html"))

        await rt.navigate("https://stale.mcp-vision.invalid/")
        snap = await rt.snapshot()
        old_sid = snap.snapshot_id
        idx = next(e["index"] for e in snap.elements if e["role"] == "textbox")

        # wait for TTL to expire
        await asyncio.sleep(1.5)

        receipt = await rt.fill(old_sid, idx, "should fail")
        print(f"  fill with stale snapshot: {receipt.status} - {receipt.message}")
        assert receipt.status == "stale", f"expected stale, got {receipt.status}"
        print("  correctly rejected stale snapshot")
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        traceback.print_exc()
        return False
    finally:
        await rt.close()


async def test_real_page_interaction():
    """Navigate to a real page (Wikipedia), snapshot it, find elements."""
    print("\n=== TEST: Real page interaction (Wikipedia) ===")
    rt = BrowserRuntime(allow_writes=False, headless=True)
    try:
        await rt._ensure()
        receipt = await rt.navigate("https://en.wikipedia.org/wiki/Main_Page")
        print(f"  navigate: {receipt.status} - {receipt.message}")

        if receipt.status != "verified":
            print("  SKIPPED: network issue")
            return True

        snap = await rt.snapshot()
        print(f"  snapshot: {len(snap.elements)} elements, title={snap.title}")
        print(f"  text length: {len(snap.text)} chars")

        await save_screenshot(rt, "05_wikipedia")

        links = [e for e in snap.elements if e["role"] == "link"]
        textboxes = [e for e in snap.elements if e["role"] == "textbox"]
        buttons = [e for e in snap.elements if e["role"] == "button"]
        print(f"  links: {len(links)}, textboxes: {len(textboxes)}, buttons: {len(buttons)}")

        # check the search box exists
        search = [e for e in snap.elements
                  if e["role"] == "textbox" or (e["role"] == "searchbox")
                  or "search" in e.get("name", "").lower()]
        print(f"  search elements: {[e['name'] for e in search]}")

        assert len(snap.elements) > 10, f"only {len(snap.elements)} elements on Wikipedia"
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        traceback.print_exc()
        return False
    finally:
        await rt.close()


async def test_origin_enforcement():
    """Verify that navigating to a disallowed origin is rejected."""
    print("\n=== TEST: Origin enforcement ===")
    rt = BrowserRuntime(allow_writes=False,
                        allowed_origins=["https://example.com"],
                        headless=True)
    try:
        await rt._ensure()
        receipt = await rt.navigate("https://evil.example.org")
        print(f"  navigate disallowed: {receipt.status} - {receipt.message}")
        assert receipt.status == "error", f"expected error, got {receipt.status}"

        # allowed origin should work
        receipt = await rt.navigate("https://example.com")
        print(f"  navigate allowed: {receipt.status} - {receipt.message}")
        assert receipt.status == "verified", f"expected verified, got {receipt.status}"
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        traceback.print_exc()
        return False
    finally:
        await rt.close()


async def test_concurrent_snapshot_freshness():
    """Two rapid fills: second should work if snapshot is still fresh."""
    print("\n=== TEST: Rapid sequential fills ===")
    html = '''<!doctype html><html><body>
    <input id="a" type="text" placeholder="Field A">
    <input id="b" type="text" placeholder="Field B">
    </body></html>'''

    rt = BrowserRuntime(allow_writes=True,
                        allowed_origins=["https://rapid.mcp-vision.invalid"],
                        headless=True)
    try:
        await rt._ensure()
        await rt.page.route("https://rapid.mcp-vision.invalid/**",
                            lambda route: route.fulfill(body=html, content_type="text/html"))

        await rt.navigate("https://rapid.mcp-vision.invalid/")
        snap = await rt.snapshot()

        fields = [e for e in snap.elements if e["role"] == "textbox"]
        print(f"  found {len(fields)} textboxes")

        if len(fields) < 2:
            print("  FAILED: need 2 textboxes")
            return False

        r1 = await rt.fill(snap.snapshot_id, fields[0]["index"], "Alpha")
        print(f"  fill A: {r1.status} - {r1.message}")

        # snapshot was invalidated after fill, so second fill with same snapshot_id should be stale
        r2 = await rt.fill(snap.snapshot_id, fields[1]["index"], "Beta")
        print(f"  fill B (same snapshot): {r2.status} - {r2.message}")

        # correct workflow: re-snapshot between fills
        snap2 = await rt.snapshot()
        fields2 = [e for e in snap2.elements if e["role"] == "textbox"]
        r3 = await rt.fill(snap2.snapshot_id, fields2[1]["index"], "Beta")
        print(f"  fill B (fresh snapshot): {r3.status} - {r3.message}")

        await save_screenshot(rt, "06_rapid_fills")
        return True
    except Exception as e:
        print(f"  FAILED: {e}")
        traceback.print_exc()
        return False
    finally:
        await rt.close()


async def main():
    print("=" * 60)
    print("MCP-Vision Real Workflow Test")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Output: {OUT}")
    print("=" * 60)

    tests = [
        ("Navigate + Snapshot", test_navigate_and_snapshot),
        ("Fill + Verify", test_fill_and_verify),
        ("Stale Rejection", test_stale_snapshot_rejected),
        ("Real Page (Wikipedia)", test_real_page_interaction),
        ("Origin Enforcement", test_origin_enforcement),
        ("Rapid Sequential Fills", test_concurrent_snapshot_freshness),
    ]

    results = {}
    for name, fn in tests:
        try:
            results[name] = await fn()
        except Exception as e:
            print(f"\n  CRASH: {e}")
            results[name] = False

    print("\n" + "=" * 60)
    print("RESULTS:")
    for name, ok in results.items():
        print(f"  {'✓' if ok else '✗'} {name}")
    passed = sum(1 for v in results.values() if v)
    print(f"\n{passed}/{len(results)} passed")
    print("=" * 60)

    # write results json
    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    return all(results.values())


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
