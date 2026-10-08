"""Typing that lands once: fields that show keys late (Chrome, Electron), never pasted on top, read back after."""
from __future__ import annotations

import asyncio
import time

from buddy_fakes import FakeHost, shot
from mcp_vision.buddy.actions import ActionContext, ActionEngine
from mcp_vision.buddy.geometry import Rect
from mcp_vision.buddy.screen_context import Control, ScreenContext


def run(coro):
    return asyncio.run(coro)


def page():
    return ScreenContext(app="Safari", window="Acme", controls=[
        Control("Back", "button", 40, 60), Control("Search", "text field", 600, 60),
        Control("Place order", "button", 1300, 900)])


def hands(context=None, host=None):
    host = host or FakeHost()
    shots = [shot()]
    context = context or page()
    context.describe(shots)
    return ActionEngine(ActionContext(host=host, screen=(shots, context))), host


class Box:
    """One text field: what's really in it, whether it's all selected, and what Accessibility reads (late)."""

    def __init__(self, value="", frame=Rect(450, 46, 300, 28)):           # page()'s Search field
        self.value, self.frame, self.selected = value, frame, False
        self.seen = [(0.0, value)]                 # (when Accessibility catches up, what it reads from then)

    def put(self, text, lag):
        self.value = text if self.selected else self.value + text
        self.selected = False                      # typing replaces a select-all; then the caret sits at the end
        self.seen.append((time.monotonic() + lag, self.value))

    def read(self):
        now = time.monotonic()
        return [value for at, value in self.seen if at <= now][-1]


class Fields(FakeHost):
    """Text fields that answer like real apps do, not instantly.

    ``lag``: Accessibility shows typing that much later (chrome, electron, busy pages); ``focus_lag``: it follows
    a click into another field that much later. ``deaf`` ignores synthetic keys, ``paste_works=False`` pastes too.
    ``hidden``: an editor typing through a tiny stand-in box (google docs, vs code) whose value never shows it.
    ``secure``: the boxes with these names are password boxes.
    """

    def __init__(self, value="old search", *, deaf=False, paste_works=True, lag=0.0, focus_lag=0.0, hidden=False,
                 boxes=None, secure=()):
        super().__init__()
        self.boxes = boxes or {"search": Box(value)}
        self.focus = next(iter(self.boxes))        # where keys land
        self.followed = (0.0, self.focus, self.focus)   # (when Accessibility's focus moves, to, from)
        self.deaf, self.paste_works, self.lag, self.focus_lag, self.hidden = deaf, paste_works, lag, focus_lag, hidden
        self.secure = set(secure)

    @property
    def value(self):
        return next(iter(self.boxes.values())).value

    def _ax(self):
        at, now, was = self.followed
        return self.boxes[now if time.monotonic() >= at else was]

    def focused_value(self):
        return "" if self.hidden else self._ax().read()

    def focused_frame(self):
        frame = self._ax().frame
        return Rect(frame.x + 8, frame.y + 6, 1, 16) if self.hidden else frame

    def focused_secure(self):
        return any(box is self._ax() for name, box in self.boxes.items() if name in self.secure)

    def click(self, x, y, button="left", count=1):
        super().click(x, y, button, count)
        hit = next((name for name, box in self.boxes.items() if box.frame.contains(x, y)), None)
        if hit is not None:
            was = next(name for name, box in self.boxes.items() if box is self._ax())
            self.focus, self.followed = hit, (time.monotonic() + self.focus_lag, hit, was)
            self.boxes[hit].selected = False

    def press(self, keys):
        super().press(keys)
        if keys == "cmd+a":
            self.boxes[self.focus].selected = True

    def type_text(self, text):
        super().type_text(text)
        if not self.deaf:
            self.boxes[self.focus].put(text, self.lag)

    def paste(self, text):
        self.calls.append(("paste", text))
        if self.paste_works:
            self.boxes[self.focus].put(text, self.lag)


def kinds(host):
    return [call[0] for call in host.calls]


def test_typing_into_a_named_field_replaces_it_and_append_keeps_it():
    host = Fields()
    e, _ = hands(host=host)
    out = run(e.handle("type_text", {"text": "usb-c hub", "id": 2}))
    assert host.value == "usb-c hub" and out.result.report == "typed into 'Search'; the field now reads 'usb-c hub'"
    host = Fields(lag=0.06)
    e, _ = hands(host=host)
    out = run(e.handle("type_text", {"text": " and more", "id": 2, "append": True}))
    assert host.value == "old search and more" and kinds(host) == ["click", "type"]
    assert out.result.report.endswith("reads 'old search and more'")


def test_a_field_that_shows_typing_late_gets_it_once_not_typed_and_pasted():
    for lag in (0.06, 0.3):                      # chrome shows keys a beat late; a check that reads once pastes on top
        host = Fields("", lag=lag)
        e, _ = hands(host=host)
        out = run(e.handle("type_text", {"text": "active", "id": 2, "submit": True}))
        assert out.status == "done" and host.value == "active", lag
        assert kinds(host) == ["click", "press", "type", "press"] and host.calls[-1] == ("press", "return")


def test_retyping_what_a_field_already_reads_leaves_it_alone_and_a_doubled_one_gets_fixed():
    host = Fields("active")
    e, _ = hands(host=host)
    out = run(e.handle("type_text", {"text": "active", "id": 2}))
    assert host.value == "active" and kinds(host) == ["click"]
    assert "already reads 'active'" in out.result.report and out.result.detail == "Already there"
    host = Fields("activeactive", lag=0.15)
    e, _ = hands(host=host)
    out = run(e.handle("type_text", {"text": "active", "id": 2}))
    assert out.status == "done" and host.value == "active" and "paste" not in kinds(host)


def test_keys_that_land_after_the_wait_get_replaced_by_the_paste_not_doubled(monkeypatch):
    from mcp_vision.buddy.actions import core

    monkeypatch.setattr(core, "VERIFY", 0.1)
    host = Fields("", lag=0.15)
    e, _ = hands(host=host)
    out = run(e.handle("type_text", {"text": "active", "id": 2}))
    assert out.status == "done" and host.value == "active"
    assert kinds(host) == ["click", "press", "type", "press", "paste"]          # select all again, then paste


def test_typing_that_doesnt_land_is_pasted_instead_and_reported_if_that_fails_too(monkeypatch):
    from mcp_vision.buddy.actions import core

    monkeypatch.setattr(core, "VERIFY", 0.1)
    host = Fields(deaf=True)
    e, _ = hands(host=host)
    out = run(e.handle("type_text", {"text": "hello", "id": 2}))
    assert out.status == "done" and host.value == "hello" and host.calls[-2:] == [("press", "cmd+a"), ("paste", "hello")]
    host = Fields(deaf=True, paste_works=False)
    e, _ = hands(host=host)
    out = run(e.handle("type_text", {"text": "hello", "id": 2}))
    assert out.status == "failed" and "didn't show up" in out.message


def test_hidden_editor_inputs_are_never_pasted_into_or_select_alled():
    host = Fields("", hidden=True)                # google docs, vs code, math boxes: the value never changes
    e, _ = hands(host=host)
    started = time.monotonic()
    out = run(e.handle("type_text", {"text": "x^2 + 1"}))
    assert out.status == "done" and host.value == "x^2 + 1" and kinds(host) == ["type"]
    assert "can't be read back: look before typing it again" in out.result.note
    assert time.monotonic() - started < 0.5                        # no waiting on a value that never shows
    out = run(e.handle("type_text", {"text": "y", "id": 2}))
    assert ("press", "cmd+a") not in host.calls and "at its cursor" in out.result.report     # not the whole doc


def test_the_second_field_in_one_reply_isnt_doubled_while_focus_catches_up():
    first, last = Box("Hussain", Rect(450, 186, 300, 28)), Box("", Rect(450, 246, 300, 28))
    host = Fields(boxes={"first": first, "last": last}, focus_lag=0.15)   # focus still on the field just typed
    form = ScreenContext(app="Google Chrome", window="Apply", controls=[
        Control("First name", "text field", 600, 200), Control("Last name", "text field", 600, 260)])
    e, _ = hands(form, host=host)
    out = run(e.handle("type_text", {"text": "Syed", "id": 2}))
    assert out.status == "done" and last.value == "Syed" and first.value == "Hussain" and "paste" not in kinds(host)


def test_the_same_thing_at_the_cursor_twice_in_one_request_types_once_but_never_reads_back_a_password():
    host = Fields("Dear Sam, ")
    e, _ = hands(host=host)
    run(e.handle("type_text", {"text": "thanks for the update"}))
    out = run(e.handle("type_text", {"text": "thanks for the update"}))
    assert host.value == "Dear Sam, thanks for the update" and kinds(host) == ["type"]
    assert "already at the end of the field" in out.result.note
    host = Fields("", secure={"search"})
    e, _ = hands(host=host)
    out = run(e.handle("type_text", {"text": "hunter22", "id": 2}))
    assert out.status == "done" and "hunter22" not in out.result.report and out.result.report == "typed into 'Search'"
