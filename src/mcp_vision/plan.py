"""Turn a user ask into a start URL / tab host — intent, not literal Google."""
from __future__ import annotations

import json
import re
from urllib.parse import quote_plus, urlsplit

# Seed aliases only. Planner logic decides *when* to use a product vs Google.
_ALIASES = {
    "github": "https://github.com",
    "gh": "https://github.com",
    "gmail": "https://mail.google.com",
    "email": "https://mail.google.com",
    "mail": "https://mail.google.com",
    "outlook": "https://outlook.live.com",
    "ebay": "https://www.ebay.com",
    "amazon": "https://www.amazon.com",
    "youtube": "https://www.youtube.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "linkedin": "https://www.linkedin.com",
    "calendar": "https://calendar.google.com",
    "drive": "https://drive.google.com",
    "notion": "https://www.notion.so",
    "slack": "https://app.slack.com",
    "reddit": "https://www.reddit.com",
    "icollege": "https://icollege.gsu.edu",
    "d2l": "https://icollege.gsu.edu",
    "gsu": "https://icollege.gsu.edu",
    "handshake": "https://app.joinhandshake.com",
    "cloudflare": "https://dash.cloudflare.com",
}

_PERSONAL = re.compile(
    r"\b(my|mine|i|i'?m|i'?ve|me|today|tonight|this week|how many|inbox|account|"
    r"profile|notifications?|activity|dashboard|what'?s on|did i|have i)\b",
    re.I,
)
_RESEARCH = re.compile(
    r"\b(what is|what'?s a|how (do|does|to|can)|why (do|does|is)|vs\.?|versus|"
    r"difference between|explain|best way|tutorial|meaning of)\b",
    re.I,
)
_SHOP = re.compile(r"\b(buy|price|cheap|under \$?\d|for sale|listing|shop)\b", re.I)
_FLIGHT = re.compile(r"\b(flight|flights|round.?trip|one.?way|airport|sfo|lax|jfk)\b", re.I)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def product_mention(query: str) -> str | None:
    """Return a canonical product key if the query names a service."""
    low = f" {query.lower()} "
    # Longer keys first so "github" wins over "git"
    for key in sorted(_ALIASES, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", low):
            return key
    return None


def origin_for(key: str) -> str:
    if key in _ALIASES:
        return _ALIASES[key]
    # Generic guess: user named a brand → try its .com (still better than googling the sentence)
    slug = re.sub(r"[^a-z0-9-]", "", key.lower())
    return f"https://www.{slug}.com" if slug else "https://www.google.com"


_PLAN_SYSTEM = (
    "You pick where a browser should open first for a user task. "
    "Reply with ONLY JSON: {\"url\": \"https://...\", \"reason\": \"short\"}. "
    "Rules: "
    "1) If they ask about THEIR account/activity/inbox/commits/orders on a named service, "
    "open that service's real site (logged-in home), never a Google search of the sentence. "
    "2) Google Search only for open-ended research (what is / how to / compare). "
    "3) Flights → Google Flights. Shopping on a named store → that store. "
    "4) Prefer https URLs with no tracking junk."
)


def plan_with_model(query: str, backend: str | None = "local") -> dict | None:
    if backend in {None, "", "none", "off", "heuristic"}:
        return None
    try:
        from backends import get_chat
        chat = get_chat(backend)
        msg = chat([
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user", "content": query},
        ], tools=None)
        raw = (msg.get("content") or "").strip()
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        data = json.loads(m.group(0))
        url = str(data.get("url") or "").strip()
        if not url.startswith("http"):
            return None
        return {"url": url, "reason": str(data.get("reason") or "model"), "source": "model"}
    except Exception:
        return None


def _best_tab(open_tabs: list[dict], host: str, *, personal: bool) -> dict | None:
    """Prefer a logged-in home/dashboard tab over a deep link on the same host."""
    scored = []
    for tab in open_tabs or []:
        url = tab.get("url") or ""
        h = _host(url)
        if not h or h != host:
            continue
        path = urlsplit(url).path or "/"
        score = 10
        if personal:
            if path in {"", "/"}:
                score += 6
            elif path.count("/") == 1:
                score += 4
            if any(p in path.lower() for p in (
                "dashboard", "home", "inbox", "notifications", "overview", "portal", "account",
            )):
                score += 5
            if path.count("/") >= 3:
                score -= 4
        scored.append((score, tab))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    best_score, best = scored[0]
    # Personal tasks need account context — don't stick on a deep resource page.
    if personal and best_score < 12:
        return None
    return best


def plan_url(query: str, *, backend: str | None = "local",
             open_tabs: list[dict] | None = None) -> dict:
    """Decide the first page to open. Prefer product sites for personal tasks."""
    q = (query or "").strip()
    low = q.lower()
    personal = bool(_PERSONAL.search(low))
    research = bool(_RESEARCH.search(low))
    product = product_mention(q)

    planned = None if product or research or _FLIGHT.search(low) else plan_with_model(q, backend)
    if planned:
        host = _host(planned["url"])
        tab = _best_tab(open_tabs, host, personal=personal) if host else None
        if tab:
            return {"url": tab["url"], "reason": f"reuse open tab for {host}",
                    "source": "tab", "tab_id": tab.get("tab_id"),
                    "expected_url": tab.get("url")}
        return planned

    if _FLIGHT.search(low):
        return {"url": "https://www.google.com/travel/flights?q=" + quote_plus(q) + "&curr=USD",
                "reason": "flight search", "source": "rule"}

    if product and (personal or not research):
        url = origin_for(product)
        host = _host(url)
        tab = _best_tab(open_tabs, host, personal=personal)
        if tab:
            return {"url": tab["url"], "reason": f"reuse open {product} tab",
                    "source": "tab", "tab_id": tab.get("tab_id"),
                    "expected_url": tab.get("url")}
        if product == "ebay" and _SHOP.search(low):
            term = re.sub(r"\bebay\b", "", q, flags=re.I).strip() or q
            return {"url": "https://www.ebay.com/sch/i.html?_nkw=" + quote_plus(term),
                    "reason": "store search", "source": "rule"}
        return {"url": url, "reason": f"open {product} (personal/account task)" if personal
                else f"open {product}", "source": "rule"}

    return {"url": "https://www.google.com/search?q=" + quote_plus(q),
            "reason": "web research" if research or not product else "fallback search",
            "source": "rule"}
