"""Real public-site probes + live-Chrome tab checks. No personal content in git."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from mcp_vision.browser import BrowserRuntime
from mcp_vision.core.governor import Governor, danger_url
from mcp_vision.live_browser import LiveBrowserRuntime
from mcp_vision.overlay.hud import set_forced_result
from mcp_vision.redaction import redact
from phase2_mcp.auth_detector import detect_auth_challenge
from phase2_mcp.session_state import VERIFIED, discover_identity
from phase2_mcp.chrome_bridge import websocket_endpoint

OUT = Path.cwd() / "outputs" / "real_tasks"


def _deny(_policy, _summary) -> bool:
    return False


def _scrub(text: str, limit: int = 400) -> str:
    text = redact(text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "[email]", text)
    text = re.sub(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "[phone]", text)
    return " ".join(text.split())[:limit]


async def _shot(runtime, name: str) -> None:
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{name}.png").write_bytes(await runtime.screenshot())
    except Exception:
        pass


async def _runtime(prefer_live: bool, headed: bool):
    if prefer_live:
        import sys
        if sys.platform == "darwin":
            from mcp_vision.native_browser import NativeBrowserRuntime
            return NativeBrowserRuntime(allow_writes=True, governor=Governor(confirmer=_deny)), "live"
        if websocket_endpoint():
            return LiveBrowserRuntime(allow_writes=True, governor=Governor(confirmer=_deny)), "live"
    channel = "chrome" if headed else None
    return (BrowserRuntime(allow_writes=True, headless=not headed, channel=channel,
                           governor=Governor(confirmer=_deny)), "isolated")


async def task_ebay(runtime) -> dict:
    name = "ebay_research"
    urls = [
        ("https://www.ebay.com/sch/i.html?_from=R40&_trksid=p2334524.m570.l1313"
         "&_nkw=mechanical+keyboard&_sacat=0"),
        "https://www.ebay.co.uk/sch/i.html?_nkw=mechanical+keyboard&_ipg=25",
        "https://www.google.com/search?tbm=shop&q=mechanical+keyboard",
    ]
    last = "no attempt"
    for url in urls:
        rec = await runtime.navigate(url)
        if rec.status != "verified":
            last = rec.message
            continue
        snap = await runtime.snapshot()
        for _ in range(5):
            if "error page" not in snap.title.lower() and (
                    re.search(r"\$\d", snap.text) or len(snap.elements) > 10):
                break
            await asyncio.sleep(1)
            snap = await runtime.snapshot()
        await _shot(runtime, name)
        prices = re.findall(r"\$\d[\d,]*\.?\d*|£\d[\d,]*\.?\d*", snap.text)
        buy = [e for e in snap.elements if re.search(
            r"\b(buy it now|add to cart|checkout|buy)\b", e["name"], re.I)]
        blocked = tested = 0
        for el in buy[:2]:
            snap = await runtime.snapshot()
            match = next((e for e in snap.elements if e["name"] == el["name"]), None)
            if not match:
                continue
            tested += 1
            if (await runtime.click(snap.snapshot_id, match["index"])).status == "blocked":
                blocked += 1
        ok = "error page" not in snap.title.lower() and (len(prices) >= 1 or len(snap.elements) > 10)
        last = _scrub(
            f"host={urlsplit(snap.url).hostname} elements={len(snap.elements)} "
            f"prices={len(prices)} buy_blocked={blocked}/{tested} title={snap.title}"
        )
        if ok:
            return {"id": name, "ok": True, "detail": last}
        await asyncio.sleep(0.8)
    return {"id": name, "ok": False, "detail": last}


async def task_flights(runtime) -> dict:
    name = "flights_sf"
    # Fixed future-ish window encoded in the URL so the probe stays deterministic.
    url = ("https://www.google.com/travel/flights?q=Flights%20to%20SFO%20on%202026-10-12%20"
           "oneway&curr=USD")
    rec = await runtime.navigate(url)
    if rec.status != "verified":
        return {"id": name, "ok": False, "detail": rec.message}
    snap = await runtime.snapshot()
    for _ in range(6):
        if re.search(r"(SFO|San Francisco|flight|results|stops|nonstop)", snap.text, re.I):
            break
        await asyncio.sleep(1.2)
        snap = await runtime.snapshot()
    await _shot(runtime, name)
    book = [e for e in snap.elements if re.search(r"\b(book|purchase|checkout|select)\b", e["name"], re.I)]
    blocked = 0
    for el in book[:2]:
        if (await runtime.click(snap.snapshot_id, el["index"])).status == "blocked":
            blocked += 1
        snap = await runtime.snapshot()
    ok = "SFO" in snap.text.upper() or "san francisco" in snap.text.lower() or len(snap.elements) > 8
    return {"id": name, "ok": ok, "detail": _scrub(
        f"elements={len(snap.elements)} bookish={len(book)} blocked={blocked} title={snap.title}"
    )}


async def task_barrier_form(runtime) -> dict:
    name = "barrier_send"
    html = """<!doctype html><title>Mail draft</title>
    <h1>Compose</h1>
    <label for=to>To</label><input id=to>
    <label for=body>Message</label><textarea id=body></textarea>
    <form action=/sent><button type=submit>Send</button></form>
    <a href="/checkout/pay">Checkout</a>
    <button type=button>Buy now</button>
    <p id=ok>draft only</p>"""
    await runtime._ensure()
    page = runtime.page
    runtime.allowed_origins = frozenset()
    if hasattr(page, "route"):
        await page.route("https://barrier.mcp-vision.test/**",
                         lambda r: r.fulfill(body=html, content_type="text/html"))
        await page.goto("https://barrier.mcp-vision.test/compose", wait_until="domcontentloaded")
        page_url = page.url
    else:
        # Native Chrome: inject the fixture page without CDP routing.
        payload = json.dumps(html)
        await runtime._run(runtime._eval, f"""(() => {{
          document.open(); document.write({payload}); document.close();
          history.replaceState({{}}, '', 'https://barrier.mcp-vision.test/compose');
          return location.href;
        }})()""")
        if runtime._current:
            runtime._current.url = "https://barrier.mcp-vision.test/compose"
        page_url = "https://barrier.mcp-vision.test/compose"
    snap = await runtime.snapshot()
    send = next(e for e in snap.elements if e["name"].lower() == "send")
    buy = next(e for e in snap.elements if "buy" in e["name"].lower())
    send_r = await runtime.click(snap.snapshot_id, send["index"])
    snap = await runtime.snapshot()
    buy_r = await runtime.click(snap.snapshot_id, buy["index"])
    ok = send_r.status == "blocked" and buy_r.status == "blocked" and danger_url(page_url)
    return {"id": name, "ok": ok, "detail": f"send={send_r.status} buy={buy_r.status} danger_url={danger_url(page_url)}"}


async def task_live_tabs(runtime, mode: str) -> dict:
    name = "live_tabs"
    if mode != "live":
        return {"id": name, "ok": True, "detail": "skipped (isolated mode); enable Chrome remote debugging for live proof"}
    listed = await runtime.tabs()
    if not listed.get("connected"):
        return {"id": name, "ok": False, "detail": listed.get("error", "not connected")}
    tabs = listed.get("tabs", [])
    hosts = []
    for t in tabs:
        m = re.match(r"https?://([^/]+)", t.get("url", ""))
        if m:
            hosts.append(m.group(1).lower())
    interesting = [h for h in hosts if any(x in h for x in (
        "mail.google", "outlook", "ebay", "google.com/travel", "icollege.gsu", "gsu.edu"))]
    return {"id": name, "ok": True, "detail": _scrub(
        f"tabs={len(tabs)} interesting_hosts={len(set(interesting))} sample={','.join(sorted(set(hosts))[:8])}"
    )}


async def task_email_observe(runtime, mode: str) -> dict:
    name = "email_check"
    if mode != "live":
        return {"id": name, "ok": True, "detail": "skipped until live Chrome (needs cookies)"}
    listed = await runtime.tabs()
    tabs = listed.get("tabs", [])
    mail = next((t for t in tabs if re.search(r"mail\.google|outlook\.live|outlook\.office", t["url"], re.I)), None)
    if mail is None:
        opened = await runtime.open_tab("https://mail.google.com/")
        if opened.status != "verified":
            return {"id": name, "ok": False, "detail": opened.message}
    else:
        used = await runtime.use_tab(mail["tab_id"], mail["url"])
        if used.status != "verified":
            return {"id": name, "ok": False, "detail": used.message}
    snap = await runtime.snapshot()
    await _shot(runtime, name)
    host = (urlsplit(snap.url).hostname or "").lower()
    challenge = detect_auth_challenge(snap.url, snap.title, snap.text, snap.elements)
    identity = discover_identity(snap.url, snap.title, snap.text, snap.elements)
    identity_verified = bool((snap.identity or {}).get("value")) or identity.source == VERIFIED
    compose = [e for e in snap.elements if re.search(r"\b(compose|send|new message)\b", e["name"], re.I)]
    blocked = tested = 0
    for el in compose[:2]:
        snap = await runtime.snapshot()
        match = next((e for e in snap.elements if e["name"] == el["name"]), None)
        if not match:
            continue
        tested += 1
        if (await runtime.click(snap.snapshot_id, match["index"])).status == "blocked":
            blocked += 1
    ok = (len(snap.text) > 40 and challenge is None
          and any(site in host for site in ("mail.google", "outlook.live", "outlook.office")))
    return {"id": name, "ok": ok, "detail": _scrub(
        f"readable={len(snap.text) > 40} correct_origin={ok or host.startswith(('mail.google', 'outlook.'))} "
        f"auth_required={challenge is not None} identity_verified={identity_verified} "
        f"compose_blocked={blocked}/{tested} title={snap.title}"
    )}


async def task_github_observe(runtime, mode: str) -> dict:
    name = "github_check"
    if mode != "live":
        return {"id": name, "ok": True, "detail": "skipped until live Chrome (needs cookies)"}
    listed = await runtime.tabs()
    tabs = listed.get("tabs", [])
    github = next((t for t in tabs if (urlsplit(t["url"]).hostname or "").lower() == "github.com"), None)
    if github:
        used = await runtime.use_tab(github["tab_id"], github["url"])
        if used.status != "verified":
            return {"id": name, "ok": False, "detail": used.message}
    else:
        opened = await runtime.open_tab("https://github.com/")
        if opened.status != "verified":
            return {"id": name, "ok": False, "detail": opened.message}
    snap = await runtime.snapshot()
    await _shot(runtime, name)
    host = (urlsplit(snap.url).hostname or "").lower()
    challenge = detect_auth_challenge(snap.url, snap.title, snap.text, snap.elements)
    identity = discover_identity(snap.url, snap.title, snap.text, snap.elements)
    identity_verified = bool((snap.identity or {}).get("value")) or identity.source == VERIFIED
    ok = host == "github.com" and len(snap.text) > 40 and challenge is None
    return {"id": name, "ok": ok, "detail": _scrub(
        f"readable={len(snap.text) > 40} correct_origin={host == 'github.com'} "
        f"auth_required={challenge is not None} identity_verified={identity_verified} title={snap.title}"
    )}


async def task_icollege(runtime, mode: str) -> dict:
    name = "icollege_week"
    if mode != "live":
        return {"id": name, "ok": True, "detail": "skipped until live Chrome (SSO cookies)"}
    listed = await runtime.tabs()
    tabs = listed.get("tabs", [])

    def score(tab):
        host = (urlsplit(tab["url"]).hostname or "").lower()
        if "gastate.view.usg.edu" in host or "d2l" in host:
            return 0
        if "icollege.gsu.edu" in host:
            return 1
        if host.endswith("gsu.edu") and not host.startswith("idp."):
            return 2
        return 99

    ranked = sorted((t for t in tabs if score(t) < 99), key=score)
    if ranked:
        used = await runtime.use_tab(ranked[0]["tab_id"], ranked[0]["url"])
        if used.status != "verified":
            return {"id": name, "ok": False, "detail": used.message}
    else:
        opened = await runtime.open_tab("https://icollege.gsu.edu/")
        if opened.status != "verified":
            return {"id": name, "ok": False, "detail": opened.message}
    snap = await runtime.snapshot()
    await _shot(runtime, name)
    host = (urlsplit(snap.url).hostname or "").lower()
    challenge = detect_auth_challenge(snap.url, snap.title, snap.text, snap.elements)
    submit = [e for e in snap.elements if re.search(r"\b(submit|post|publish)\b", e["name"], re.I)]
    blocked = tested = 0
    for el in submit[:2]:
        snap = await runtime.snapshot()
        match = next((e for e in snap.elements if e["name"] == el["name"]), None)
        if not match:
            continue
        tested += 1
        if (await runtime.click(snap.snapshot_id, match["index"])).status == "blocked":
            blocked += 1
    correct_origin = any(site in host for site in ("icollege.gsu.edu", "gastate.view.usg.edu", "d2l"))
    ok = len(snap.text) > 40 and correct_origin and challenge is None
    return {"id": name, "ok": ok, "detail": _scrub(
        f"readable={len(snap.text) > 40} correct_origin={correct_origin} "
        f"auth_required={challenge is not None} submit_blocked={blocked}/{tested} title={snap.title}"
    )}


async def run_probe(*, prefer_live: bool = False, headed: bool = False) -> bool:
    OUT.mkdir(parents=True, exist_ok=True)
    set_forced_result(False)  # never pop dialogs during automated probes
    from mcp_vision.challenge import set_forced_challenge_result
    set_forced_challenge_result(True)  # captcha pauses auto-continue in probes
    runtime, mode = await _runtime(prefer_live, headed)
    results = []
    try:
        if mode == "live":
            listed = await runtime.tabs()
            if not listed.get("connected"):
                results.append({"id": "connect", "ok": False, "detail": listed.get("error", "fail")})
            else:
                results.append({"id": "connect", "ok": True, "detail": f'tabs={len(listed["tabs"])}'})
                # Prefer a fresh tab so we do not clobber the user's current page.
                opened = await runtime.open_tab("https://example.com")
                results.append({"id": "open_tab", "ok": opened.status == "verified", "detail": opened.message})
                if opened.status != "verified":
                    (OUT / "results.json").write_text(json.dumps(
                        {"mode": mode, "results": results, "passed": 0, "total": len(results)}, indent=2) + "\n")
                    print(json.dumps({"mode": mode, "results": results}, indent=2))
                    return False
        results.append(await task_barrier_form(runtime))
        results.append(await task_ebay(runtime))
        results.append(await task_flights(runtime))
        results.append(await task_live_tabs(runtime, mode))
        results.append(await task_github_observe(runtime, mode))
        results.append(await task_email_observe(runtime, mode))
        results.append(await task_icollege(runtime, mode))
    finally:
        set_forced_result(None)
        from mcp_vision.challenge import set_forced_challenge_result
        set_forced_challenge_result(None)
        await runtime.close()

    report = {"mode": mode, "results": results, "passed": sum(1 for r in results if r["ok"]),
              "total": len(results)}
    (OUT / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    # Live-only skips count as ok; hard failures must not.
    return all(r["ok"] for r in results)
