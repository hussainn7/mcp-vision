"""What the model should know besides pixels: app, window, selection, controls.

The "screen map" lists real on-screen controls with their exact positions in
each screenshot's pixel space. Vision models use it to aim precisely instead
of estimating; text-only engines (no image input) use it to point at all.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Protocol

from mcp_vision.buddy.geometry import Screenshot

MAX_CONTROLS = 70
_DIGITS = re.compile(r"\d+")
SELECTION_LIMIT = 3000        # characters of selected text the model sees (about 750 tokens at most)


@dataclass(frozen=True)
class Control:
    label: str
    role: str
    x: float          # center, global top-left points
    y: float


@dataclass
class ScreenContext:
    app: str = ""
    window: str = ""
    selection: str = ""
    controls: list[Control] = field(default_factory=list)
    focused: str = ""             # the text box typing goes into, like 'search field "Search"'
    selection_chars: int = 0      # how long the selection really is; ``selection`` keeps SELECTION_LIMIT of it
    ids: dict[int, Control] = field(default_factory=dict, compare=False, repr=False)   # [n] in the last describe

    @property
    def selection_cut(self) -> bool:
        """The model sees only the start of what's selected, so it can't rewrite it in place."""
        return self.selection_chars > len(self.selection)

    @property
    def empty(self) -> bool:
        return not (self.app or self.window or self.selection or self.controls)

    def find(self, text: str) -> Control | None:
        """The control whose label is ``text``: an exact match first, then the shortest label containing it."""
        needle = " ".join(text.lower().split())
        if not needle:
            return None
        labelled = [(" ".join(c.label.lower().split()), c) for c in self.controls]
        exact = [c for label, c in labelled if label == needle]
        if exact:
            return exact[0]
        partial = sorted((c for label, c in labelled if needle in label), key=lambda c: len(c.label))
        return partial[0] if partial else None

    def signature(self) -> str:
        """Changes when what's on screen changes (app, window, controls and where they are)."""
        parts = [self.app, self.window, *(f"{c.label}|{c.role}|{round(c.x)}|{round(c.y)}" for c in self.controls)]
        return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:16]

    def settle_signature(self) -> str:
        """For "has it stopped changing?": ``signature`` minus what moves on its own.

        A clock, a counter or "2 min ago" on the page kept it from ever looking settled, so every step there
        waited out the whole limit. Digits in labels are masked and positions snapped to a 16-point grid; the
        window title counts as it is ("Loading 2 of 5" is still loading).
        """
        parts = [self.app, self.window,
                 *(f"{_DIGITS.sub('#', c.label)}|{c.role}|{int(c.x) // 16}|{int(c.y) // 16}" for c in self.controls)]
        return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:16]

    def content_signature(self) -> str:
        """What's on the page, not where: a navigation that loads nothing new keeps it (going in circles)."""
        parts = [f"{c.label}|{c.role}" for c in self.controls if "address" not in c.label.lower()]
        return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:16]

    def describe(self, shots: list[Screenshot]) -> str:
        """Compact text block for the user turn; coordinates in screenshot pixels."""
        lines: list[str] = []
        if self.app:
            lines.append(f"frontmost app: {self.app}" + (f' (window "{self.window[:80]}")' if self.window else ""))
        if self.focused:
            lines.append(f"typing goes into: {self.focused}")
        selected = _tidy(self.selection[:SELECTION_LIMIT])
        if selected:
            # Line breaks kept: "rewrite this" on an email has to give its paragraphs back.
            cut = (f" (only the first {len(self.selection):,} of {self.selection_chars:,} characters)"
                   if self.selection_cut else "")
            quoted = f' "{selected}"' if "\n" not in selected else f'\n"""\n{selected}\n"""'
            lines.append(f"selected text{cut}:{quoted}")
        mapped = _map_controls(self.controls, shots)
        self.ids = {}
        if mapped:
            lines.append("controls on screen ([id] label | role | x,y = center in that screenshot's pixels):")
            for screen_label, items in mapped.items():
                lines.append(f"  {screen_label}:")
                for control, label, role, x, y in items:
                    number = len(self.ids) + 1
                    self.ids[number] = control
                    lines.append(f"    [{number}] {label} | {role} | {x},{y}")
        return "\n".join(lines)


def _tidy(text: str) -> str:
    """Selected text with its line breaks, minus runs of spaces and blank lines."""
    lines = [" ".join(line.split()) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _map_controls(controls: list[Control], shots: list[Screenshot]
                  ) -> dict[str, list[tuple[Control, str, str, int, int]]]:
    out: dict[str, list[tuple[Control, str, str, int, int]]] = {}
    seen: set[tuple[str, int, int]] = set()
    for control in controls[: MAX_CONTROLS * 2]:
        shot = next((s for s in shots if s.screen.frame.contains(control.x, control.y)), None)
        if shot is None:
            continue
        px, py = shot.from_global(control.x, control.y)
        key = (control.label, round(px), round(py))
        if key in seen:
            continue
        seen.add(key)
        label = " ".join(control.label.split())[:60].replace("|", "/")
        out.setdefault(shot.screen.label, []).append((control, label, control.role, round(px), round(py)))
        if sum(len(items) for items in out.values()) >= MAX_CONTROLS:
            break
    return out


class ContextProvider(Protocol):
    def snapshot(self) -> ScreenContext: ...      # called on a worker thread


class NullContext:
    def snapshot(self) -> ScreenContext:
        return ScreenContext()
