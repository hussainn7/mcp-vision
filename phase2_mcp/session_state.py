"""Verified application state vs model guesses.

The LLM can propose URLs and identities. This module decides what is safe
to navigate and what must be discovered from the live page.

Sources: USER_ASSERTION | INFERRED | OBSERVED | VERIFIED
Unknown is valid. Never promote INFERRED to a fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from phase2_mcp.auth_detector import detect_auth_challenge

USER_ASSERTION = "USER_ASSERTION"
INFERRED = "INFERRED"
OBSERVED = "OBSERVED"
VERIFIED = "VERIFIED"

# First-path words that are app chrome, not people/orgs/docs.
# Not a site list — common web vocabulary so /settings stays a fast path.
_GENERIC_SEGS = frozenset({
    "about", "account", "accounts", "admin", "api", "app", "apps", "auth",
    "blog", "cart", "c", "channel", "channels", "checkout", "collections",
    "dashboard", "discover", "docs", "explore", "feed", "feeds", "help",
    "home", "i", "in", "inbox", "issues", "login", "logout", "mail", "me",
    "messages", "new", "notifications", "oauth", "orgs", "pricing",
    "privacy", "profile", "projects", "pulls", "search", "session",
    "sessions", "settings", "signin", "signout", "signup", "stars",
    "static", "support", "terms", "topics", "u", "user", "users",
    "watching", "welcome", "www",
})

_SIGNED_IN_RE = re.compile(
    r"(?:signed in as|logged in as|using account)\s+[@:]?\s*([A-Za-z0-9._@+-]{2,64})",
    re.I,
)
_IDENTITY_NAME_RE = re.compile(
    r"(?:account(?:\s+menu)?|user(?:\s+menu)?|profile(?:\s+menu)?|avatar|"
    r"view (?:your )?profile|signed in|google account|accounts? menu)",
    re.I,
)
_AUTHED_HINTS = ("sign out", "log out", "your profile", "account menu", "signed in")
_LOGGED_OUT_HINTS = ("sign in", "log in", "create account", "create an account")
_IDENTITY_TASK_RE = re.compile(
    r"\bmy\s+(?:[A-Za-z0-9._-]+\s+)?"
    r"(?:account|profile|user\s*name|handle|login|identity)\b|"
    r"\b(?:which|what)\s+account\b|"
    r"\b(?:logged|signed)\s+in\b|"
    r"\bwho\s+am\s+i\b",
    re.I,
)


@dataclass
class Claim:
    value: str | None
    source: str
    via: str = ""


@dataclass
class NavDecision:
    url: str
    kind: str  # exact | canonical | rewrite | blocked
    source: str
    original: str
    entity: str | None = None
    note: str = ""
    needs_identity: bool = False


@dataclass
class Cycle:
    claim: str = ""
    evidence: str = ""
    confidence: str = INFERRED
    action: str = ""
    observation: str = ""
    verify: str = "pending"  # pass | fail | pending


@dataclass
class SessionState:
    task: str = ""
    url: str = ""
    title: str = ""
    application: str = ""
    authenticated: bool | None = None
    identity: Claim = field(default_factory=lambda: Claim(None, INFERRED))
    identities: dict = field(default_factory=dict)  # host -> Claim
    facts: list = field(default_factory=list)  # (kind, value, via)
    identity_changed: bool = False
    workspace: Claim = field(default_factory=lambda: Claim(None, INFERRED))
    last_decision: NavDecision | None = None
    last_cycle: Cycle = field(default_factory=Cycle)
    mismatch: str = ""
    auth_required: bool = False
    ledger: list[tuple[str, str]] = field(default_factory=list)

    def reset(self, task: str = "") -> None:
        self.__dict__.update(SessionState(task=task).__dict__)

    def note(self, source: str, text: str) -> None:
        item = (source, text)
        if item not in self.ledger:
            self.ledger.append(item)


STATE = SessionState()


def bind_task(task: str) -> None:
    STATE.reset(task or "")


def identity_verified(host: str | None = None) -> str | None:
    """Page-backed identity for this application. Other sites do not count."""
    h = (host or STATE.application or "").lower().removeprefix("www.")
    c = STATE.identities.get(h) if h else None
    if c is None and h and h == (STATE.application or "").lower().removeprefix("www."):
        c = STATE.identity
    if not c or not c.value:
        return None
    if c.source == VERIFIED:
        return c.value
    if c.source == OBSERVED and c.via != "url path":
        return c.value
    return None


def task_needs_identity(task: str | None = None) -> bool:
    t = (task if task is not None else STATE.task) or ""
    return _IDENTITY_TASK_RE.search(t) is not None


def _host(url: str) -> str:
    raw = url if "://" in url else f"https://{url}"
    return urlparse(raw).netloc.lower().removeprefix("www.")


def _origin(url: str) -> str:
    raw = url if "://" in url else f"https://{url}"
    p = urlparse(raw)
    host = p.netloc or p.path.split("/")[0]
    scheme = p.scheme or "https"
    return f"{scheme}://{host}"


def _path_segs(url: str) -> list[str]:
    raw = url if "://" in url else f"https://{url}"
    path = urlparse(raw).path or ""
    return [s for s in path.split("/") if s]


def _is_generic_seg(seg: str) -> bool:
    s = seg.lower()
    if s in _GENERIC_SEGS:
        return True
    if s.isdigit() and len(s) <= 4:
        return True
    if "." in s and s.rsplit(".", 1)[-1] in {"html", "htm", "php", "aspx"}:
        return True
    return False


def entity_segs(url: str) -> list[str]:
    return [s for s in _path_segs(url) if not _is_generic_seg(s)]


_HEDGE_RE = re.compile(
    r"(?:i think|i believe|i guess|probably|maybe|might be|not sure|"
    r"pretty sure|should be|i assume)\s+"
    r"(?:that\s+)?"
    r"(?:my\s+)?(?:username|user name|handle|account|profile|url)?\s*"
    r"(?:is|at|:)?\s*[`'\"]?([A-Za-z0-9._/@-]+)[`'\"]?",
    re.I,
)


def hedged_tokens(task: str | None = None) -> set[str]:
    """Guesses the user hedged — not facts."""
    found: set[str] = set()
    for m in _HEDGE_RE.finditer(task or ""):
        raw = (m.group(1) or "").strip().strip("/").lstrip("@").rstrip(".,;:`'\"").lower()
        if not raw:
            continue
        found.add(raw.split("/")[-1])
        found.add(raw)
        if "://" in raw:
            found.add(urlparse(raw if "://" in raw else "https://" + raw).path.strip("/").split("/")[-1])
    return {t for t in found if t}


def _task_has(token: str, task: str) -> bool:
    if not token or not task:
        return False
    tok = token.lower().rstrip("/")
    if tok in hedged_tokens(task):
        return False
    t = task.lower()
    if tok in t:
        return True
    return re.search(rf"\b{re.escape(tok)}\b", t) is not None


def user_supplied_destination(url: str, task: str) -> bool:
    """True when the user literally included this URL (or host+path)."""
    if not url or not task:
        return False
    t = task.lower()
    raw = url if "://" in url else f"https://{url}"
    p = urlparse(raw)
    path = (p.path or "/").rstrip("/") or "/"
    no_scheme = f"{p.netloc}{path}".lower().rstrip("/")
    full = raw.lower().rstrip("/")
    if full in t or no_scheme in t:
        return True
    if path != "/" and no_scheme in t.replace("https://", "").replace("http://", ""):
        return True
    return False


def gate_navigate(url: str, task: str | None = None) -> NavDecision:
    """Rewrite inferred personalized URLs to the application root.

    Fast path: exact user URL, or canonical host/app chrome with no entity slug.
    Discovery path: entity slug not in the user task → origin only.
    """
    task = STATE.task if task is None else task
    raw = (url or "").strip()
    if not raw:
        return NavDecision("", "blocked", INFERRED, raw, note="ERROR: empty URL")
    if "://" not in raw:
        raw = "https://" + raw

    if user_supplied_destination(raw, task):
        d = NavDecision(raw, "exact", USER_ASSERTION, raw)
        if entity_segs(raw) and not identity_verified(_host(raw)):
            d.needs_identity = True
            d.note = (
                "User supplied this URL. It is not yet verified as their account. "
                "Observe the signed-in identity before treating it as 'mine'."
            )
        STATE.last_decision = d
        STATE.note(USER_ASSERTION, f"destination {raw}")
        STATE.last_cycle = Cycle(
            claim=raw, evidence="user-supplied URL", confidence=USER_ASSERTION,
            action="allow", verify="pending",
        )
        return d

    entities = entity_segs(raw)
    if not entities:
        d = NavDecision(raw, "canonical", USER_ASSERTION if _task_has(_host(raw), task) else OBSERVED, raw)
        STATE.last_decision = d
        STATE.note(d.source, f"canonical {raw}")
        STATE.last_cycle = Cycle(
            claim=raw, evidence="application root / app chrome", confidence=d.source,
            action="allow", verify="pending",
        )
        return d

    owned = [e for e in entities if _task_has(e, task)]
    if len(owned) == len(entities):
        d = NavDecision(raw, "exact", USER_ASSERTION, raw, entity=entities[0])
        STATE.last_decision = d
        STATE.note(USER_ASSERTION, f"named resource {raw}")
        STATE.last_cycle = Cycle(
            claim=raw, evidence="named in user task", confidence=USER_ASSERTION,
            action="allow", verify="pending",
        )
        return d

    ident = identity_verified(_host(raw))
    if ident and entities[0].lower() == ident.lower():
        d = NavDecision(raw, "exact", VERIFIED, raw, entity=ident)
        d.note = f"Allowed {raw}: identity {ident} is VERIFIED from the page."
        STATE.last_decision = d
        STATE.note(VERIFIED, f"personal URL allowed for {ident}")
        STATE.last_cycle = Cycle(
            claim=raw, evidence=f"identity {ident} ({STATE.identity.via})",
            confidence=VERIFIED, action="allow", verify="pending",
        )
        return d

    guessed = [e for e in entities if not _task_has(e, task)]
    origin = _origin(raw)
    guess = guessed[0] if guessed else entities[0]
    d = NavDecision(
        origin,
        "rewrite",
        INFERRED,
        raw,
        entity=guess,
        needs_identity=True,
        note=(
            f"UNVERIFIED ASSERTION: {guess!r} in {raw} has no page evidence. "
            f"Navigating to {origin} instead. Observe who is signed in, then retry."
        ),
    )
    STATE.last_decision = d
    STATE.note(INFERRED, f"blocked personalized path {raw}")
    STATE.note(OBSERVED, f"using application root {origin}")
    STATE.last_cycle = Cycle(
        claim=f"identity={guess}", evidence="none", confidence=INFERRED,
        action=f"rewrite → {origin}", verify="fail",
        observation="guess rejected until the page confirms it",
    )
    return d


def _identity_from_elements(elements: list[dict]) -> Claim | None:
    for el in elements or []:
        name = str(el.get("name") or el.get("label") or "")
        if not name or not _IDENTITY_NAME_RE.search(name):
            continue
        # "Account menu for ada" / "Google Account: Ada (ada@x.com)"
        m = re.search(
            r"(?:for|as|:)\s+([A-Za-z0-9._@+-]{2,64})",
            name,
            re.I,
        )
        if m:
            return Claim(m.group(1).strip().strip("()"), OBSERVED, "account menu")
        handle = re.search(r"@([A-Za-z0-9._-]{2,64})", name)
        if handle:
            return Claim(handle.group(1), OBSERVED, "account menu")
    return None


def discover_identity(
    url: str = "",
    title: str = "",
    text: str = "",
    elements: list | None = None,
) -> Claim:
    """Who is this application treating as the current user? URL slug is never VERIFIED."""
    m = _SIGNED_IN_RE.search(text or "")
    if m:
        return Claim(m.group(1).rstrip(".,;:"), VERIFIED, "signed-in text")
    hit = _identity_from_elements(elements or [])
    if hit:
        hit.source = VERIFIED if hit.via == "account menu" else OBSERVED
        return hit
    m = _SIGNED_IN_RE.search(title or "")
    if m:
        return Claim(m.group(1).rstrip(".,;:"), OBSERVED, "title")
    segs = entity_segs(url)
    if segs:
        return Claim(segs[0], OBSERVED, "url path")
    return Claim(None, INFERRED)


def _auth_flag(url: str, title: str, text: str, elements: list | None) -> bool | None:
    blob = f"{title}\n{text}\n" + " ".join(
        str(e.get("name") or "") for e in (elements or [])
    )
    low = blob.lower()
    if any(h in low for h in _AUTHED_HINTS):
        return True
    ch = detect_auth_challenge(url, title, text, elements)
    if ch:
        return False
    if any(h in low for h in _LOGGED_OUT_HINTS) and "sign out" not in low and "log out" not in low:
        return False
    return None


def observe_page(
    url: str = "",
    title: str = "",
    text: str = "",
    elements: list | None = None,
) -> str:
    """Update STATE from what the browser actually shows. Returns a short tag for the model."""
    if url:
        STATE.url = url
        STATE.application = _host(url)
    if title:
        STATE.title = title

    ch = detect_auth_challenge(url or STATE.url, title or STATE.title, text, elements)
    STATE.auth_required = bool(ch)
    if ch:
        STATE.authenticated = False
        STATE.note(OBSERVED, f"auth challenge {ch.challenge_type}")
        return f"[session] AUTH_REQUIRED ({ch.challenge_type}). Do not invent an account. Wait or use the sign-in page."

    auth = _auth_flag(url or STATE.url, title or STATE.title, text, elements)
    if auth is not None:
        STATE.authenticated = auth

    ident = discover_identity(url or STATE.url, title or STATE.title, text, elements)
    if ident.value:
        if ident.source == OBSERVED and ident.via == "url path" and STATE.identity.value:
            pass
        else:
            if ident.via == "url path" and ident.source == OBSERVED:
                STATE.note(OBSERVED, f"url slug {ident.value} (not verified)")
                if not STATE.identity.value:
                    STATE.identity = ident
            else:
                prev = identity_verified(STATE.application)
                STATE.identity = ident
                STATE.identities[STATE.application] = ident
                STATE.note(ident.source, f"identity {ident.value} via {ident.via}")
                if prev and ident.value and prev.lower() != ident.value.lower():
                    STATE.identity_changed = True
                    STATE.note(OBSERVED, f"stale identity {prev} → {ident.value}")

    STATE.mismatch = ""
    segs = entity_segs(STATE.url)
    verified = identity_verified(STATE.application)
    if verified and segs and segs[0].lower() != verified.lower() and not _is_generic_seg(segs[0]):
        STATE.mismatch = (
            f"Page identity is {verified!r} but the URL slug is {segs[0]!r}. "
            "Application state wins. Re-resolve from the application root; do not trust the guessed URL."
        )
        STATE.note(VERIFIED, STATE.mismatch)
        STATE.last_cycle.verify = "fail"
        STATE.last_cycle.observation = STATE.mismatch
        return f"[session] MISMATCH: {STATE.mismatch}"

    parts = [f"app={STATE.application or '?'}"]
    if STATE.authenticated is True:
        parts.append("authenticated=true")
    elif STATE.authenticated is False:
        parts.append("authenticated=false")
    if STATE.identity.value:
        parts.append(f"identity={STATE.identity.value} ({STATE.identity.source})")
    else:
        parts.append("identity=unknown")
    tag = "[session] " + " ".join(parts)
    cyc = STATE.last_cycle
    cyc.observation = tag
    if identity_verified():
        cyc.verify = "pass"
        cyc.evidence = f"{STATE.identity.via or STATE.identity.source}: {STATE.identity.value}"
        cyc.confidence = VERIFIED
    elif STATE.last_decision and STATE.last_decision.needs_identity:
        cyc.verify = "pending"
        tag += " Identity unknown — inspect the signed-in account UI. Do not guess."
    return tag


_CLAIM_IN_TEXT = re.compile(
    r"(?:username|user name|handle|account(?: name)?|logged in as|signed in as|"
    r"profile (?:is|of)|your github|the user(?:name)?)\s*(?:is\s*)?[:@]?\s*"
    r"([A-Za-z0-9._-]{2,64})",
    re.I,
)


def recovery_prompt() -> str | None:
    """What the model must do next. None = no forced recovery."""
    if STATE.auth_required:
        return "VERIFY FAILED: sign-in or challenge page. Do not invent an account."
    if STATE.mismatch:
        return f"VERIFY FAILED: {STATE.mismatch}"
    if STATE.last_decision and STATE.last_decision.needs_identity and not identity_verified():
        return (
            "Identity is unknown. Observe the signed-in account UI "
            "(web_snapshot / web_read). Do not navigate to a guessed profile."
        )
    return None


def reject_unverified_answer(answer: str) -> str | None:
    """Block a final answer that states an identity the page never confirmed."""
    if not task_needs_identity():
        return None
    text = answer or ""
    claimed = [m.group(1) for m in _CLAIM_IN_TEXT.finditer(text)]
    ident = identity_verified()
    if claimed:
        guess = claimed[0]
        if not ident:
            return (
                f"UNVERIFIED ASSERTION: you stated identity {guess!r} with no page evidence. "
                "Observe the signed-in account UI, then answer from that."
            )
        if guess.lower() != ident.lower() and ident.lower() not in guess.lower():
            return (
                f"VERIFY FAILED: you stated {guess!r} but the page identity is {ident!r}. "
                "The page wins. Re-observe and correct the answer."
            )
        return None
    if not ident:
        return (
            "Identity is still unknown. Do not invent a username. "
            "web_snapshot / web_read the account UI first."
        )
    return None


def note_fact(kind: str, value: str, via: str) -> None:
    STATE.facts.append((kind, value, via))
    STATE.note(OBSERVED, f"{kind}={value} via {via}")


def conflict_report(kind: str = "project") -> str | None:
    vals = [(v, via) for k, v, via in STATE.facts if k == kind]
    uniq = {v.lower(): (v, via) for v, via in vals}
    if len(uniq) < 2:
        return None
    parts = [f"{v} ({via})" for v, via in uniq.values()]
    msg = "CONFLICT: " + kind + " evidence disagrees: " + " vs ".join(parts) + ". Do not pick a winner."
    STATE.note(OBSERVED, msg)
    return msg


def recovery_record() -> dict:
    d = STATE.last_decision
    blocked = bool(d and d.kind == "rewrite")
    verified = identity_verified()
    if STATE.mismatch or STATE.identity_changed:
        recovery = "successful"
        final = "verified" if verified else "conflict"
    elif blocked and verified:
        recovery = "successful"
        final = "verified"
    elif blocked:
        recovery = "pending"
        final = "unverified"
    elif conflict_report():
        recovery = "successful"
        final = "conflict"
    elif verified:
        recovery = "n/a"
        final = "verified"
    else:
        recovery = "n/a"
        final = "unverified"
    return {
        "initial_assumption": (d.entity or d.original) if d else "",
        "evidence": STATE.last_cycle.evidence or "none",
        "action": "BLOCKED" if blocked else "ALLOWED",
        "observation": (STATE.last_cycle.observation or "")[:200],
        "recovery": recovery,
        "final": final,
        "identity": verified or "unknown",
        "identity_changed": STATE.identity_changed,
        "mismatch": bool(STATE.mismatch),
    }


def format_debug() -> str:
    s = STATE
    ident = s.identity
    d = s.last_decision
    lines = [
        "[STATE]",
        f"Application: {s.application or '(unknown)'}",
        f"URL: {s.url or '(none)'}",
        f"Authenticated: {s.authenticated}",
        f"Identity: {ident.value or '(unknown)'}",
        f"Identity source: {ident.via or ident.source}",
        f"Identity confidence: {ident.source}",
        "",
        "[CYCLE]",
        f"CLAIM: {s.last_cycle.claim or '(none)'}",
        f"EVIDENCE: {s.last_cycle.evidence or '(none)'}",
        f"CONFIDENCE: {s.last_cycle.confidence}",
        f"ACTION: {s.last_cycle.action or '(none)'}",
        f"OBSERVATION: {s.last_cycle.observation or '(none)'}",
        f"VERIFY: {s.last_cycle.verify}",
        "",
        "[NAVIGATION]",
    ]
    if d:
        lines += [
            f"Requested: {d.original}",
            f"Destination: {d.url}",
            f"Source: {d.source}",
            f"Decision: {d.kind.upper()}",
            f"Needs identity: {d.needs_identity}",
        ]
    else:
        lines.append("(no navigation yet)")
    if s.mismatch:
        lines += ["", "[MISMATCH]", s.mismatch]
    if s.auth_required:
        lines += ["", "[AUTH]", "sign-in or challenge page observed"]
    lines += ["", "[TASK]", repr(s.task), "", "ASSUMPTIONS"]
    buckets = {USER_ASSERTION: [], VERIFIED: [], OBSERVED: [], INFERRED: []}
    for src, text in s.ledger:
        buckets.setdefault(src, []).append(text)
    for label in (VERIFIED, OBSERVED, USER_ASSERTION, INFERRED):
        lines.append(f"[{label}]")
        items = buckets.get(label) or ["(none)"]
        lines.extend(f"- {x}" for x in items)
    return "\n".join(lines)


def demo():
    bind_task("Check my GitHub and find my latest repository.")
    d = gate_navigate("https://github.com/someoneelse")
    assert d.kind == "rewrite" and d.url == "https://github.com", d
    d = gate_navigate("https://github.com")
    assert d.kind == "canonical", d
    d = gate_navigate("https://github.com/notifications")
    assert d.kind == "canonical", d
    bind_task("Open https://github.com/torvalds/linux")
    d = gate_navigate("https://github.com/torvalds/linux")
    assert d.kind == "exact", d
    bind_task("find my latest post")
    d = gate_navigate("https://www.linkedin.com/in/guessed-name")
    assert d.kind == "rewrite" and "linkedin.com" in d.url, d
    bind_task("Open Gmail")
    d = gate_navigate("https://mail.google.com")
    assert d.kind == "canonical", d
    bind_task("check my messages")
    d = gate_navigate("https://mail.google.com/mail/u/99")
    assert d.kind == "canonical", d  # u + number are generic
    bind_task("who am I")
    tag = observe_page(
        url="https://example.com/home",
        title="Inbox",
        text="Signed in as ada.lovelace\nWelcome back",
        elements=[{"role": "button", "name": "Account menu for ada.lovelace"}],
    )
    assert STATE.identity.value == "ada.lovelace", STATE.identity
    assert STATE.identity.source == VERIFIED
    assert "identity=ada.lovelace" in tag
    d = gate_navigate("https://example.com/ada.lovelace/docs")
    assert d.kind == "exact" and d.source == VERIFIED, d
    d = gate_navigate("https://example.com/someone-else")
    assert d.kind == "rewrite", d
    assert reject_unverified_answer("Your username is someone-else") 
    assert reject_unverified_answer("3 repos found") is None
    bind_task("open https://example.com/other-person")
    gate_navigate("https://example.com/other-person")
    observe_page(url="https://example.com/other-person", text="Signed in as ada.lovelace")
    assert STATE.mismatch
    print("ok")


if __name__ == "__main__":
    demo()
