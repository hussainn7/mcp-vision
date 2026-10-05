"""What Plip knows about you, kept on your Mac.

Facts come from your own data (Contacts card, browser autofill, Mail
accounts, the memory you paste from ChatGPT or Claude) and from
things you tell Plip to remember. Each fact keeps its source. Sensitive ones
(passport, card numbers, SSN) are stored but never sent to the model; Plip
asks before using them.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mcp_vision.buddy.store import config_dir

# Keys Plip understands, in the order they appear on the knowledge panel.
KEYS = {
    "name.full": "Name", "name.first": "First name", "name.last": "Last name", "email": "Email",
    "phone": "Phone", "address.street": "Street", "address.city": "City", "address.state": "State",
    "address.postal": "ZIP / postal code", "address.country": "Country", "birthday": "Birthday",
    "company": "Company", "title": "Job title", "website": "Website", "linkedin": "LinkedIn",
    "github": "GitHub", "twitter": "X / Twitter", "school": "School", "note": "Note",
}
MULTI = {"email", "phone", "note", "website"}
SOURCE_RANK = {"you": 0, "contacts": 1, "autofill": 2, "mail": 3, "imessage": 4, "chatgpt": 5, "claude": 5,
               "gemini": 5, "ai": 5}
SOURCE_LABELS = {"you": "You told Plip", "contacts": "Contacts", "autofill": "Browser autofill", "mail": "Mail",
                 "imessage": "iMessage", "chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini",
                 "ai": "AI memory"}
SENSITIVE_RE = re.compile(r"passport|social security|\bssn\b|card number|credit card|debit card|\bcvv\b|\bcvc\b|"
                          r"routing|account number|iban|password|passcode|\bpin\b|driver'?s? licen[cs]e|tax id",
                          re.IGNORECASE)


def looks_sensitive(key: str, value: str) -> bool:
    if SENSITIVE_RE.search(key) or SENSITIVE_RE.search(value):
        return True
    for candidate in re.findall(r"(?:\d[ -]?){13,19}", value):       # card-like numbers anywhere
        digits = re.sub(r"\D", "", candidate)
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return True
    return bool(re.search(r"\b\d{3}-\d{2}-\d{4}\b", value))          # SSN shape


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        number = int(char)
        if index % 2:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def normalize(key: str, value: str) -> str:
    value = " ".join(str(value).split())
    if key == "email":
        return value.lower()
    if key == "phone":
        return re.sub(r"[^\d+]", "", value)
    return value.casefold()


def mask(value: str) -> str:
    return "•••• " + value[-4:] if len(value) > 4 else "••••"


@dataclass
class Fact:
    key: str
    value: str
    sources: list[str] = field(default_factory=list)
    sensitive: bool = False
    at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    @property
    def rank(self) -> int:
        return min((SOURCE_RANK.get(source, 9) for source in self.sources), default=9)

    def card(self) -> dict:
        return {"id": self.id, "key": self.key, "label": KEYS.get(self.key, self.key.replace(".", " ").title()),
                "value": mask(self.value) if self.sensitive else self.value, "sensitive": self.sensitive,
                "sources": [SOURCE_LABELS.get(source, source) for source in self.sources]}


class Memory:
    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "memory.json"
        self.facts: list[Fact] = []
        self.imports: dict[str, dict] = {}          # source -> {"count", "at", "error"}
        self.contacts: list[dict] = []              # people you message most: {"name"?, "handle", "count"}
        self.handles: list[str] = []                # your own iMessage phone numbers / emails
        self.load()

    # -- persistence -----------------------------------------------------------------
    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        # Skip fields from newer builds so an older app still opens the same memory file.
        known = Fact.__dataclass_fields__
        self.facts = [Fact(**{k: v for k, v in item.items() if k in known})
                      for item in data.get("facts", []) if item.get("key") and item.get("value")]
        self.imports = data.get("imports", {})
        self.contacts = data.get("contacts", [])
        self.handles = data.get("handles", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"facts": [asdict(fact) for fact in self.facts], "imports": self.imports,
                                   "contacts": self.contacts, "handles": self.handles}, indent=1))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    # -- editing ------------------------------------------------------------------------------
    def add(self, key: str, value: str, source: str = "you", sensitive: bool | None = None) -> Fact | None:
        key = key.strip().lower()
        value = " ".join(str(value).split())
        if not key or not value or len(value) > 500:
            return None
        if key not in KEYS:
            value, key = f"{key.replace('_', ' ')}: {value}", "note"
        wanted = normalize(key, value)
        for fact in self.facts:
            if fact.key == key and normalize(fact.key, fact.value) == wanted:
                if source not in fact.sources:
                    fact.sources.append(source)
                return fact
        # Single-valued keys may hold alternatives; best() picks by source, then recency.
        fact = Fact(key, value, [source], looks_sensitive(key, value) if sensitive is None else sensitive)
        self.facts.append(fact)
        return fact

    def remove(self, fact_id: str) -> bool:
        before = len(self.facts)
        self.facts = [fact for fact in self.facts if fact.id != fact_id]
        return len(self.facts) != before

    def forget_source(self, source: str) -> int:
        removed = 0
        for fact in list(self.facts):
            if source in fact.sources:
                fact.sources.remove(source)
                if not fact.sources:
                    self.facts.remove(fact)
                    removed += 1
        self.imports.pop(source, None)
        return removed

    def merge(self, source: str, facts: list[tuple[str, str]], error: str = "") -> int:
        added = 0
        for key, value in facts:
            before = len(self.facts)
            if self.add(key, value, source) is not None and len(self.facts) > before:
                added += 1
        self.imports[source] = {"count": len(facts), "added": added, "at": int(time.time()), "error": error}
        return added

    # -- reading ---------------------------------------------------------------------------------
    def best(self, key: str) -> Fact | None:
        candidates = [fact for fact in self.facts if fact.key == key]
        return min(candidates, key=lambda fact: (fact.rank, -fact.at)) if candidates else None

    def values(self, key: str) -> list[str]:
        facts = sorted((fact for fact in self.facts if fact.key == key), key=lambda fact: (fact.rank, -fact.at))
        return [fact.value for fact in facts]

    def profile(self) -> dict[str, str]:
        """Best value per key (sensitive ones excluded)."""
        out: dict[str, str] = {}
        for key in KEYS:
            if key == "note":
                continue
            fact = self.best(key)
            if fact is not None and not fact.sensitive:
                out[key] = fact.value
        if "name.full" not in out and ("name.first" in out or "name.last" in out):
            out["name.full"] = " ".join(filter(None, [out.get("name.first"), out.get("name.last")]))
        if "name.full" in out and "name.first" not in out:
            parts = out["name.full"].split()
            if len(parts) >= 2:
                out["name.first"], out["name.last"] = parts[0], parts[-1]
        return out

    def summary(self, limit: int = 1800) -> str:
        """Compact block for the model: what it may use to personalize and fill forms."""
        profile = self.profile()
        if not profile and not self.facts:
            return ""
        lines = ["about the user (their own saved details; use for forms and personal questions, never read "
                 "them all out):"]
        for key, value in profile.items():
            if key in {"name.first", "name.last"} and "name.full" in profile:
                continue
            extra = self.values(key)[1:3] if key in MULTI else []
            lines.append(f"- {KEYS[key].lower()}: {value}" + (f" (also {', '.join(extra)})" if extra else ""))
        notes = [fact for fact in sorted(self.facts, key=lambda fact: (fact.rank, -fact.at)) if fact.key == "note"]
        for fact in notes[:25]:
            lines.append(f"- {'(sensitive, ask before using) ' + fact.value.split(':')[0] if fact.sensitive else fact.value}")
        sensitive = [fact for fact in self.facts if fact.sensitive and fact.key != "note"]
        if sensitive:
            lines.append("- saved but private (ask first): " + ", ".join(sorted({KEYS.get(f.key, f.key) for f in sensitive})))
        text = "\n".join(lines)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def panel(self) -> dict:
        """Knowledge-panel data for the dashboard."""
        profile = self.profile()
        return {
            "profile": profile,
            "facts": [fact.card() for fact in sorted(self.facts, key=lambda fact: (list(KEYS).index(fact.key)
                                                                                     if fact.key in KEYS else 99,
                                                                                     fact.rank, -fact.at))],
            "imports": self.imports,
            "contacts": self.contacts[:12],
            "handles": self.handles,
        }


__all__ = ["KEYS", "SOURCE_LABELS", "Fact", "Memory", "looks_sensitive", "mask"]
