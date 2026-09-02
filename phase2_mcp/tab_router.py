"""Pick an existing tab or open a new one. Never hijack an unrelated page."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class Route:
    action: str  # reuse | new_tab | navigate
    index: int | None = None


def _netloc(url: str) -> str:
    if not url:
        return ""
    raw = url if "://" in url else f"https://{url}"
    return urlparse(raw).netloc.lower()


def _is_blank(url: str) -> bool:
    u = (url or "").lower()
    return (not u) or u == "about:blank" or u.startswith("chrome://newtab") or u.startswith("chrome://inspect")


def match_tab(pages: list[dict], target: str) -> int | None:
    """Index of the best existing tab for target URL/title, or None."""
    target_clean = (target or "").lower().strip()
    if not target_clean or not pages:
        return None
    aliases = {
        "gmail": ("mail.google.com", "gmail.com", "inbox.google.com"),
        "mail.google.com": ("gmail.com", "inbox.google.com"),
        "gmail.com": ("mail.google.com",),
    }
    want_host = _netloc(target_clean)
    extra = aliases.get(target_clean.rstrip("/").split("/")[0], ())
    if want_host:
        extra = extra + aliases.get(want_host, ())

    for i, p in enumerate(pages):
        url = (p.get("url") or "").lower()
        if target_clean in url:
            return i
        if any(a in url for a in extra):
            return i

    if want_host:
        for i, p in enumerate(pages):
            host = _netloc(p.get("url") or "")
            if host == want_host or want_host in host or host.endswith("." + want_host):
                return i

    for i, p in enumerate(pages):
        title = (p.get("title") or "").lower()
        if target_clean in title:
            return i
    return None


def decide_route(pages: list[dict], target: str, current_index: int = 0) -> Route:
    """reuse matching tab, else new_tab if current has work, else navigate current."""
    hit = match_tab(pages, target)
    if hit is not None:
        return Route("reuse", hit)
    if not pages:
        return Route("new_tab")
    cur = pages[current_index] if 0 <= current_index < len(pages) else pages[0]
    if _is_blank(cur.get("url") or ""):
        return Route("navigate", current_index if pages else None)
    return Route("new_tab")


def demo():
    pages = [
        {"url": "https://github.com/foo", "title": "GitHub"},
        {"url": "https://mail.google.com/mail/u/0/#inbox", "title": "Inbox"},
        {"url": "https://www.amazon.com/", "title": "Amazon"},
        {"url": "https://www.google.com/search?q=hi", "title": "hi - Google Search"},
    ]
    r = decide_route(pages, "https://mail.google.com", current_index=0)
    assert r.action == "reuse" and r.index == 1, r

    r = decide_route(pages, "https://news.ycombinator.com", current_index=0)
    assert r.action == "new_tab", r

    blank = [{"url": "about:blank", "title": ""}]
    r = decide_route(blank, "https://example.com", current_index=0)
    assert r.action == "navigate" and r.index == 0, r

    assert match_tab(pages, "gmail") == 1
    assert match_tab(pages, "https://github.com") == 0
    print("ok")


if __name__ == "__main__":
    demo()
