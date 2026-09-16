"""Turn a user ask into a start URL / tab host — intent, not literal Google."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from urllib.parse import quote_plus, urlsplit

# Seed aliases only. Planner logic decides *when* to use a product vs Google.
_ALIASES = {
    "google": "https://www.google.com",
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
    r"\b(research|look up|what is|what'?s a|how (do|does|to|can)|why (do|does|is)|vs\.?|versus|"
    r"difference between|explain|best way|tutorial|meaning of)\b",
    re.I,
)
_SHOP = re.compile(r"\b(buy|price|cheap|under \$?\d|for sale|listing|shop)\b", re.I)
_FLIGHT = re.compile(r"\b(flight|flights|round.?trip|one.?way|airport|sfo|lax|jfk)\b", re.I)

# Any of these means the user has *decided* the travel window without pinning a
# specific date — Google Flights is allowed to (and must) pick one for us.
_ANY_DATES = re.compile(
    r"\b(anytime|flexible|whenever|any\s+(?:dates?|days?|time|week|month)|open.?ended|"
    r"your (?:choice|call|pick)|whatever|pick\s+(?:the\s+dates|any))\b",
    re.I,
)
# A concrete or Google-parsable departure/return signal is already given.
_HAS_DATE = re.compile(
    r"\b(?:today|tomorrow|this week|next week|this weekend|next weekend|this month|next month|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}|\d{1,2}\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?|\d{1,4}[-/]\d{1,2})\b",
    re.I,
)


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
    "2) Public web search only for open-ended research (what is / how to / compare). "
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


def _flight_term(q: str) -> str:
    """A Google-read flight search phrase with origin/destination + concrete dates.

    Google Flights only shows offer cards once a *concrete* date range is set;
    without one it just shows the search form (which a read-only mission cannot
    fill). When the user defers ("pick any dates", "flexible") or leaves the
    dates out, we pick a sensible upcoming long weekend so real results render.
    """
    q = (q or "").strip()
    relative = _relative_flight_dates(q)
    if relative:
        start, end, phrase = relative
        # Give Google concrete dates rather than hoping its query parser and our
        # verifier interpret "next week" identically.
        clean = re.sub(
            r"\b(?:today|tomorrow|this week|next week|this weekend|next weekend)\b",
            "", q, flags=re.I,
        )
        clean = re.sub(r"\s+", " ", clean).strip(" ,")
        rendered = start.strftime("%b %d").replace(" 0", " ")
        if end != start:
            rendered += " to " + end.strftime("%b %d, %Y").replace(" 0", " ")
        else:
            rendered += ", " + str(start.year)
        return f"{clean} {rendered}".strip()
    if _HAS_DATE.search(q):
        return re.sub(r"\s+", " ", q).strip()
    # No date signal yet: build a clean origin → destination phrase and let
    # Google Flights render real offer cards for a picked long weekend.
    stop = r"(?=\s+(?:to|from|on|in|at|for|returning|departing|arriving|with|next|this|sep|oct|stay|whenever|anytime|flexible|any)\b|[,;!.?]|$)"
    origin = re.search(r"\bfrom\s+([a-z][a-z0-9 \-']*?)" + stop, q, re.I)
    dest = re.search(r"\bto\s+([a-z][a-z0-9 \-']*?)" + stop, q, re.I)
    o = (re.sub(r"\s+", " ", origin.group(1)).strip() if origin else "")
    d = (re.sub(r"\s+", " ", dest.group(1)).strip() if dest else "")
    if not o:
        before_to = re.search(r"\b([a-z][a-z0-9 \-']*?)\s+to\b", q, re.I)
        o = (re.sub(r"\s+", " ", before_to.group(1)).strip() if before_to else "")
    if not d:
        after_from = re.search(r"\bfrom\s+([a-z][a-z0-9 \-']*?)\s*$", q, re.I)
        d = (re.sub(r"\s+", " ", after_from.group(1)).strip() if after_from else "")
    phrase = ("flights from " + o) if o else "flights"
    if d:
        phrase += " to " + d
    # Pick next Thursday out, four days back (a typical long weekend) so
    # Google Flights actually renders offer cards.
    days = (3 - date.today().weekday()) % 7 or 7
    d1 = date.today() + timedelta(days=days)
    d2 = d1 + timedelta(days=4)
    fmt = lambda d: d.strftime("%b %d").replace(" 0", " ")
    return f"{phrase} {fmt(d1)} to {fmt(d2)}"


def _relative_flight_dates(q: str, today: date | None = None) -> tuple[date, date, str] | None:
    """Resolve common relative travel windows once, for planning and proof."""
    today = today or date.today()
    low = (q or "").lower()
    if re.search(r"\btomorrow\b", low):
        day = today + timedelta(days=1)
        return day, day, "tomorrow"
    if re.search(r"\bnext week\b", low):
        start = today + timedelta(days=(7 - today.weekday()))
        return start, start + timedelta(days=6), "next week"
    if re.search(r"\bthis week\b", low):
        start = today + timedelta(days=1)
        end = today + timedelta(days=max(1, 6 - today.weekday()))
        return start, end, "this week"
    weekend = re.search(r"\b(next|this) weekend\b", low)
    if weekend:
        days_to_friday = (4 - today.weekday()) % 7
        if weekend.group(1) == "next" or days_to_friday == 0:
            days_to_friday += 7
        start = today + timedelta(days=days_to_friday)
        return start, start + timedelta(days=2), weekend.group(0)
    return None


def _flight_search_url(q: str) -> str:
    return "https://www.google.com/travel/flights?q=" + quote_plus(_flight_term(q)) + "&curr=USD"


def plan_url(query: str, *, backend: str | None = "local",
             open_tabs: list[dict] | None = None) -> dict:
    """Decide the first page to open. Prefer product sites for personal tasks."""
    q = (query or "").strip()
    low = q.lower()
    personal = bool(_PERSONAL.search(low))
    research = bool(_RESEARCH.search(low))
    product = product_mention(q)
    if product == "google" and low != "google":
        product = None

    # Unknown destinations start at search. Model-invented deep links can be
    # stale or nonexistent; only observed result links are navigated afterward.
    if _FLIGHT.search(low):
        return {"url": _flight_search_url(q),
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

    # General search engines frequently present automation challenges to
    # disposable browsers. Wikipedia's search is a stable, content-rich first
    # source for ordinary research; explicit Google and time-sensitive requests
    # still use a general search engine.
    if research and not re.search(
            r'\b(?:now|currently|current|latest|newest|recent(?:ly)?|today|tonight|tomorrow|'
            r'yesterday|upcoming|next|this (?:week|month|quarter|year|season)|news|google)\b',
            low):
        term = re.sub(r'^\s*(?:research|look up)\s+', '', q, flags=re.I).strip() or q
        search = "https://en.wikipedia.org/w/index.php?search="
        q = term
    else:
        search = ("https://www.google.com/search?q=" if re.search(r'\bgoogle\b', low)
                  else "https://www.bing.com/search?q=")
    return {"url": search + quote_plus(q),
            "reason": "web research" if research or not product else "fallback search",
            "source": "rule"}
