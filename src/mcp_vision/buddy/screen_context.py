"""What the model knows besides pixels: app, page, selection, controls, text.

The "screen map" gives exact screenshot-pixel positions so models aim instead of guessing;
with the page text, a step often needs no screenshot.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from mcp_vision.buddy.geometry import Rect, Screenshot

MAX_CONTROLS = 70
MAX_TEXTS = 40
TEXT_BUDGET = 1400            # characters of visible text per look
VALUE_SHOWN = 60              # chars of a field's value (a text area's last ones)
_DIGITS = re.compile(r"\d+")
SELECTION_LIMIT = 3000        # chars of selection the model sees (~750 tokens)
HIDDEN_INPUT = 6              # px: thinner text box = editor stand-in (Docs, VS Code)
# labels of fields whose values never reach the model
SECRET = re.compile(r"pass(word|code|phrase|port)|\bpin\b|\bcard\b|cvv|cvc|security (code|answer|question)|\bssn\b|"
                    r"social security|secret|api key|access key|private key|token|one.time code|verification code|"
                    r"(login|sign.in|auth\w*|\d.digit) code|\b2fa\b|\botp\b|account (number|no\b)|bank account|"
                    r"routing|\biban\b|sort code|\btax ?id|taxpayer|national id|licen[cs]e number|"
                    r"(seed|recovery|backup|secret) (phrase|words|codes?)|mnemonic", re.IGNORECASE)


def looks_secret(label: str) -> bool:
    """Never sent to the model: labeled like a password/card/code/key, or a mostly-digit placeholder."""
    return bool(SECRET.search(label or "")) or len(re.sub(r"\D", "", label or "")) >= 8


@dataclass(frozen=True)
class Control:
    label: str
    role: str
    x: float          # center, global top-left points
    y: float
    w: float = 0.0
    h: float = 0.0
    value: str = ""   # typed text ("" for passwords), kept out of label
    secure: bool = False      # password box, whatever its label


@dataclass
class ScreenContext:
    app: str = ""
    window: str = ""
    url: str = ""                 # page address (browsers)
    selection: str = ""
    controls: list[Control] = field(default_factory=list)
    texts: list[Control] = field(default_factory=list)          # visible static text, with positions
    scroll_areas: list[Control] = field(default_factory=list)   # scrollable regions
    focused: str = ""             # where typing goes, e.g. 'search field "Search"'
    selection_chars: int = 0      # full length (``selection`` is capped)
    blind: bool = False           # a web app whose page isn't in the map (yet)
    window_frame: Rect | None = None      # focused window, global top-left points
    ids: dict[int, Control] = field(default_factory=dict, compare=False, repr=False)   # [n] in the last describe

    @property
    def selection_cut(self) -> bool:
        """Selection was truncated, so it can't be rewritten in place."""
        return self.selection_chars > len(self.selection)

    @property
    def empty(self) -> bool:
        return not (self.app or self.window or self.selection or self.controls or self.texts)

    @property
    def rich(self) -> bool:
        """Enough non-menu structure for a text-only look to do; never when blind."""
        if self.blind:
            return False
        content = [control for control in self.controls if control.role != "menu"]
        return len(content) >= 8 or (len(content) >= 4 and len(self.texts) >= 6)

    def find(self, text: str) -> Control | None:
        """Best match for ``text``: exact > prefix > whole word > substring; controls win ties over text."""
        needle = " ".join(text.lower().split())
        if not needle:
            return None
        word = re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")
        best: tuple[int, int, int] | None = None
        found: Control | None = None
        for order, item in enumerate([*self.controls, *self.texts]):
            label = " ".join(item.label.lower().split())
            if needle not in label:
                continue
            rank = 0 if label == needle else 1 if label.startswith(needle) else 2 if word.search(label) else 3
            key = (rank, 1 if item.role == "text" else 0, order)
            if best is None or key < best:
                best, found = key, item
        return found

    def signature(self, values: bool = True, skip: Collection[str] = (), digits: bool = True) -> str:
        """Hash of what's on screen; ``values`` adds typed text, except in ``skip`` fields (by ``_spot``)."""
        parts = [self.app, self.window, self.url,
                 *(_spot(c) + (f"|{c.value}" if values and _spot(c) not in skip else "") for c in self.controls),
                 *(t.label for t in self.texts)]
        text = "\n".join(parts)
        if not digits:                                # "saved 4 seconds ago" ticking isn't a change
            text = _DIGITS.sub("#", text)
        return hashlib.sha1(text.encode()).hexdigest()[:16]

    def holding(self, texts: Iterable[str]) -> set[str]:
        """Fields (by ``_spot``) showing one of these texts, matched by start or end."""
        pieces = {piece for text in texts if _loose(text) for piece in (_loose(text)[:40], _loose(text)[-40:])}
        return {_spot(c) for c in self.controls if c.value and any(piece in _loose(c.value) for piece in pieces)}

    def settle_signature(self) -> str:
        """``signature`` for settling: digits masked (clocks, "2 min ago"), positions snapped to 16 pt."""
        parts = [self.app, self.window, self.url,
                 *(f"{_DIGITS.sub('#', c.label)}|{c.role}|{int(c.x) // 16}|{int(c.y) // 16}" for c in self.controls),
                 *(_DIGITS.sub("#", t.label) for t in self.texts)]
        return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:16]

    def content_signature(self) -> str:
        """Page content only (no positions or address): a no-op navigation keeps it."""
        parts = [*(f"{c.label}|{c.role}" for c in self.controls if "address" not in c.label.lower()),
                 *(t.label for t in self.texts)]
        return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:16]

    def describe(self, shots: list[Screenshot]) -> str:
        """Compact text block for the user turn; coordinates in screenshot pixels."""
        lines: list[str] = []
        if self.app:
            lines.append(f"frontmost app: {self.app}" + (f' (window "{self.window[:80]}")' if self.window else ""))
        if self.url:
            lines.append(f"page: {self.url[:240]}")
        if self.focused:
            lines.append(f"typing goes into: {self.focused}")
        selected = _tidy(self.selection[:SELECTION_LIMIT])
        if selected:
            # line breaks kept so a rewrite keeps paragraphs
            cut = (f" (only the first {len(self.selection):,} of {self.selection_chars:,} characters)"
                   if self.selection_cut else "")
            quoted = f' "{selected}"' if "\n" not in selected else f'\n"""\n{selected}\n"""'
            lines.append(f"selected text{cut}:{quoted}")
        bar = [control for control in self.controls if control.role == "menu"]
        mapped = _map_controls([control for control in self.controls if control.role != "menu"], shots)
        self.ids = {}
        if mapped:
            lines.append("controls on screen ([id] label | role | x,y = center in that screenshot's pixels):")
            single = len(shots) <= 1                  # one screen: skip headings and indents
            for screen_label, items in mapped.items():
                if not single:
                    lines.append(f"  {screen_label}:")
                for control, label, x, y in items:
                    number = len(self.ids) + 1
                    self.ids[number] = control
                    if control.value.strip() and not control.secure and not looks_secret(control.label):
                        # a text area shows its end, where typing goes
                        shown = _clip(control.value, VALUE_SHOWN, end=control.role == "text area")
                        label += f' = "{shown}"'
                    lines.append(f"{'' if single else '    '}[{number}] {label} | {control.role} | {x},{y}")
        if self.blind:
            lines.append("note: this app isn't showing its page to plip's controls list yet, so the page itself isn't "
                         "listed: work from the screenshot (x,y) and read_page")
        menus = _map_controls(bar, shots, limit=20)
        if menus:
            names = [f"{label} {x},{y}" for items in menus.values() for _, label, x, y in items]
            lines.append("menu bar (click by name): " + " · ".join(names))
        areas = _map_controls(self.scroll_areas, shots, limit=6)
        if areas:
            names = [f"{label} at {x},{y}" for items in areas.values() for _, label, x, y in items]
            lines.append("scrollable: " + "; ".join(names))
            if any("% down)" in name or "to the bottom)" in name for name in names):
                lines.append("note: the page is scrolled down, so it starts above what's listed here")
        said = {" ".join(label.lower().split()) for items in mapped.values() for _, label, _, _ in items}
        visible = _visible_text(self.texts, said)
        if visible:
            lines.append(f"visible text: {visible}")
        return "\n".join(lines)


def _spot(control: Control) -> str:
    return f"{control.label}|{control.role}|{round(control.x)}|{round(control.y)}"


_CURLY = str.maketrans("\u2018\u2019\u201c\u201d", "''\"\"")


def _loose(text: str) -> str:
    """Normalize spacing and the curly quotes apps swap in."""
    return " ".join(text.translate(_CURLY).split())


def _clip(text: str, limit: int, end: bool = False) -> str:
    text = " ".join(text.split()).replace("|", "/")
    if len(text) <= limit:
        return text
    return "…" + text[1 - limit:] if end else text[: limit - 1] + "…"


def _tidy(text: str) -> str:
    """Keep line breaks; squeeze spaces and blank lines."""
    lines = [" ".join(line.split()) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _map_controls(controls: list[Control], shots: list[Screenshot], limit: int = MAX_CONTROLS
                  ) -> dict[str, list[tuple[Control, str, int, int]]]:
    out: dict[str, list[tuple[Control, str, int, int]]] = {}
    seen: set[tuple[str, int, int]] = set()
    count = 0
    for control in controls[: limit * 2]:
        shot = next((s for s in shots if s.screen.frame.contains(control.x, control.y)), None)
        if shot is None:
            continue
        px, py = shot.from_global(control.x, control.y)
        key = (control.label, round(px), round(py))
        if key in seen:
            continue
        seen.add(key)
        label = " ".join(control.label.split())[:60].replace("|", "/")
        out.setdefault(shot.screen.label, []).append((control, label, round(px), round(py)))
        count += 1
        if count >= limit:
            break
    return out


def _visible_text(texts: list[Control], said: set[str] | None = None) -> str:
    """Page text, deduped and minus what listed controls already say."""
    chunks: list[str] = []
    used = 0
    seen: set[str] = set(said or ())
    for item in texts[:MAX_TEXTS * 2]:
        text = " ".join(item.label.split())
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        text = text[:160]
        if used + len(text) > TEXT_BUDGET:
            break
        chunks.append(text)
        used += len(text) + 3
        if len(chunks) >= MAX_TEXTS:
            break
    return " · ".join(chunks)


def flatten_page(items: list[tuple[str, str]]) -> list[str]:
    """Document-order ``(kind, text)`` -> lines; ``"break"`` starts a new line, repeats dropped."""
    lines: list[str] = []
    current: list[str] = []
    for kind, text in items:
        if kind == "break":
            if current:
                lines.append(" · ".join(current))
                current = []
            continue
        text = " ".join(str(text).split())[:300]
        if text and (not current or current[-1] != text):
            current.append(text)
    if current:
        lines.append(" · ".join(current))
    out: list[str] = []
    for line in lines:
        if not out or out[-1] != line:
            out.append(line)
    return out


def page_excerpt(lines: list[str], *, find: str = "", start: int = 0, limit: int = 6000) -> tuple[str, int]:
    """Page text from ``start``, or only lines near ``find``; returns ``(text, total chars)``."""
    if find.strip():
        words = [word for word in find.lower().split() if len(word) > 2] or [find.lower().strip()]
        keep: set[int] = set()
        for index, line in enumerate(lines):
            lowered = line.lower()
            if any(word in lowered for word in words):
                keep.update({index - 1, index, index + 1})
        lines = [lines[index] for index in sorted(keep) if 0 <= index < len(lines)]
    text = "\n".join(lines)
    return text[max(0, start): max(0, start) + limit], len(text)


class ContextProvider(Protocol):
    def snapshot(self) -> ScreenContext: ...      # called on a worker thread


class NullContext:
    def snapshot(self) -> ScreenContext:
        return ScreenContext()
