"""Scrolls hit the panel they mean, check they moved, try other ways before giving up, and say what happened.

From plip: a plain scroll aims at the panel last clicked in (while still on that page), the focused one, the
pointer they moved, then the biggest area; it watches the pixels there and the map; when nothing moves it tries the
panel's own scroll bar, more spots and page keys (only where they scroll); a click by number after a scroll finds
the control where it moved to.
"""
from __future__ import annotations

import asyncio

import pytest

from buddy_fakes import FakeHost, shot
from mcp_vision.buddy.actions import ActionContext, ActionEngine
from mcp_vision.buddy.geometry import Rect
from mcp_vision.buddy.screen_context import Control, ScreenContext


def run(coro):
    return asyncio.run(coro)


def page(scrolled=0):
    """A long web page: 'Pricing' only comes into view after three scrolls."""
    texts = [Control(f"Section {scrolled + n}", "text", 700, 200 + 80 * n) for n in range(4)]
    if scrolled >= 3:
        texts.append(Control("Pricing", "text", 700, 500))
    return ScreenContext(
        app="Safari", window="Acme",
        controls=[Control("Back", "button", 40, 60, 30, 24), Control("Search", "text field", 600, 60, 300, 28),
                  Control("Place order", "button", 1300, 900, 120, 36)],
        texts=texts,
        scroll_areas=[Control("page", "scroll area", 756, 520, 1512, 880), Control("sidebar", "scroll area", 100, 500, 200, 800)])


class Site:
    """Context provider that scrolls with the host."""

    def __init__(self, host, end=99):
        self.host, self.end = host, end

    def snapshot(self):
        scrolls = min(sum(1 for call in self.host.calls if call[0] == "scroll"), self.end)
        return page(scrolls)


def wheel(host):
    return [call for call in host.calls if call[0] == "scroll"]


def hands(context=None, host=None, observe=None, animate=None):
    host = host or FakeHost()
    shots = [shot()]
    context = context or page()
    context.describe(shots)
    ctx = ActionContext(host=host, screen=(shots, context))
    ctx.observe = observe or (lambda: None)
    if animate is not None:
        ctx.animate = animate
    return ActionEngine(ctx), host


def test_scroll_aims_at_the_biggest_area_and_obeys_direction_and_amount():
    e, host = hands()
    run(e.handle("scroll", {}))
    run(e.handle("scroll", {"direction": "up", "amount": 2}))
    run(e.handle("scroll", {"direction": "down", "amount": "half", "id": 2}))
    assert host.calls == [("scroll", 756, 520, 8, 0), ("scroll", 756, 520, -16, 0), ("scroll", 600, 60, 4, 0)]


def test_scroll_to_finds_text_locally_without_the_model():
    host = FakeHost()
    site = Site(host)
    glides = []
    e, _ = hands(host=host, observe=site.snapshot, animate=lambda x, y, label: glides.append(label) or 0.0)
    out = run(e.handle("scroll_to", {"text": "Pricing"}))
    assert out.status == "done" and "found 'Pricing' after 3 scrolls" in out.result.report
    assert [call[0] for call in host.calls] == ["scroll_to_visible"] + ["scroll"] * 3 and glides == ["Pricing"]


def test_a_field_that_keeps_changing_isnt_the_page_scrolling():
    ticks = iter(range(1000))

    def live():                                     # a terminal's output or a timer: its value changes every look
        return ScreenContext(app="Terminal", controls=[Control("shell", "text area", 600, 400, 800, 600,
                                                               value=f"tick {next(ticks)}")],
                             scroll_areas=[Control("shell", "scroll area", 600, 400, 800, 600)])
    e, host = hands(live(), observe=live)
    assert run(e.handle("scroll", {})).result.detail == "Nothing moved"
    out = run(e.handle("scroll_to", {"text": "Pricing", "direction": "down"}))
    assert "nothing moved" in out.result.report and "to the end" not in out.result.report   # not "reached the end"


def test_scroll_to_stops_at_the_end_of_the_page():
    host = FakeHost()
    e, _ = hands(host=host, observe=Site(host, end=1).snapshot)
    out = run(e.handle("scroll_to", {"text": "Pricing", "direction": "down"}))
    assert "to the end" in out.result.report and len(wheel(host)) == 3


def test_scroll_to_turns_around_at_the_bottom_when_no_direction_was_given():
    host = FakeHost()
    e, _ = hands(host=host, observe=Site(host, end=1).snapshot)
    out = run(e.handle("scroll_to", {"text": "Pricing"}))
    directions = [call[3] > 0 for call in wheel(host)]
    assert "both ends" in out.result.report and directions[:3] == [True, True, True] and False in directions


def cart_page(scrolled=0, frame=None, areas=True):
    texts = [Control(f"Section {scrolled + n}", "text", 700, 200 + 80 * n) for n in range(4)]
    return ScreenContext(
        app="Safari", window="Acme", window_frame=frame,
        controls=[Control("Back", "button", 40, 60, 30, 24),
                  Control("Add to cart", "button", 700, 800 - 300 * min(scrolled, 1), 120, 36)],
        texts=texts,
        scroll_areas=[Control("page", "scroll area", 756, 520, 1512, 880)] if areas else [])


def test_a_scroll_that_moves_nothing_says_so():
    engine, _ = hands(cart_page(), observe=lambda: cart_page())
    out = run(engine.handle("scroll", {"direction": "down"}))
    assert "nothing moved" in out.result.report and "bottom" in out.result.report


def test_click_by_number_after_a_scroll_finds_the_button_where_it_is_now():
    host = FakeHost()
    scrolled = lambda: cart_page(1 if any(call[0] == "scroll" for call in host.calls) else 0)    # noqa: E731
    engine, _ = hands(cart_page(), host=host, observe=scrolled)
    run(engine.handle("scroll", {"direction": "down"}))
    run(engine.handle("click", {"id": 2}))                    # [2] was "Add to cart" at y=800 before the scroll
    assert host.calls[-1] == ("click", 700, 500)


def test_without_scroll_areas_plip_scrolls_the_front_window_not_the_middle_of_the_screen():
    engine, host = hands(cart_page(areas=False, frame=Rect(0, 0, 800, 600)), observe=lambda: None)
    run(engine.handle("scroll", {}))
    assert host.calls[0][:3] == ("scroll", 400, 360)


def deals(offset=0):
    """Rows of deals, each with its own "Add to cart"; scrolling moves every row up by ``offset``."""
    controls, texts = [], []
    for row, name in enumerate(["Echo Dot", "Kindle", "Soundcore Mini", "Fire TV Stick"]):
        y = 200 + row * 150 - offset
        texts.append(Control(name, "text", 600, y))
        controls.append(Control("Add to cart", "button", 600, y + 40, 120, 30))
    return ScreenContext(app="Safari", window="Deals", controls=controls, texts=texts)


def test_a_repeated_button_is_refound_by_how_far_the_page_moved():
    host = FakeHost()
    moved = lambda: deals(150 if any(call[0] == "scroll" for call in host.calls) else 0)    # noqa: E731
    engine, _ = hands(deals(), host=host, observe=moved)
    run(engine.handle("scroll", {"direction": "down"}))
    run(engine.handle("click", {"id": 3}))                    # [3] = Soundcore Mini's button at y=540 before
    assert host.calls[-1] == ("click", 600, 390)


def test_near_picks_the_button_in_that_items_row():
    engine, host = hands(deals(), observe=lambda: deals())
    run(engine.handle("click", {"text": "add to cart", "near": "soundcore"}))
    assert host.calls[-1] == ("click", 600, 540)


def test_scroll_all_the_way_keeps_going_until_the_page_stops():
    host = FakeHost()
    engine, _ = hands(cart_page(), host=host, observe=Site(host, end=4).snapshot)
    out = run(engine.handle("scroll", {"direction": "up", "amount": "all"}))
    assert out.result.report == "scrolled to the top" and 4 <= len(host.calls) <= 6
    assert all(call[3] == -60 for call in host.calls)


def mail(scrolled=0, url="", app="Chrome", window="Mail", frame=None):
    """Web mail: the email list is a side panel inside the page (what they call "the Email panel"). Its rows
    move up a slot per scroll."""
    rows = [Control(f"Invoice {n}", "link", 1300, 200 + 80 * (n - scrolled), 300, 40)
            for n in range(scrolled, scrolled + 5)]
    return ScreenContext(
        app=app, window=window, url=url, window_frame=frame or Rect(0, 0, 1512, 960),
        controls=[Control("Compose", "button", 80, 120, 100, 30), *rows],
        texts=[Control("Welcome back", "text", 600, 400)],
        scroll_areas=[Control("Mail - Inbox (3)", "page", 756, 520, 1512, 880),
                      Control("Email (scrolled 40% down)", "scroll area", 1300, 520, 400, 760)])


@pytest.fixture
def quick(monkeypatch):
    """No real waiting between a scroll and checking whether it moved."""
    from mcp_vision.buddy.actions import control

    monkeypatch.setattr(control.time, "sleep", lambda seconds: None)


def test_after_a_click_in_a_side_panel_the_wheel_goes_to_the_middle_of_that_panel_and_says_so():
    engine, host = hands(mail(), observe=lambda: None)
    out = run(engine.handle("scroll", {"direction": "up"}))
    assert host.calls == [("scroll", 756, 520, -8, 0)] and out.result.report == "scrolled up"   # nothing clicked yet
    engine, host = hands(mail(), observe=lambda: None)
    run(engine.handle("click", {"id": 2}))                                   # an email in the list
    out = run(engine.handle("scroll", {"direction": "up"}))
    run(engine.handle("scroll", {"direction": "down", "id": 3}))             # or named: anything inside it
    assert host.calls == [("click", 1300, 200), ("scroll", 1300, 520, -8, 0), ("scroll", 1300, 280, 8, 0)]
    assert out.result.report == ("scrolled the 'Email' panel up; for another panel give its x,y or the id of "
                                 "anything inside it")


def test_without_a_click_the_focused_panel_then_the_pointer_they_moved_beat_the_biggest_area():
    host = FakeHost()
    host.focused_area, host.mouse = Rect(1100, 140, 400, 760), (100, 500)
    engine, _ = hands(mail(), host=host, observe=lambda: None)
    run(engine.handle("scroll", {}))
    host.focused_area = None
    run(engine.handle("scroll", {}))                                         # they moved it there: a hint
    run(engine.handle("scroll", {}))                                         # now it's where plip left it: not one
    host.mouse = (3000, 500)                                                 # outside their window: not one either
    run(engine.handle("scroll", {}))
    assert wheel(host) == [("scroll", 1300, 520, 8, 0), ("scroll", 100, 500, 8, 0), ("scroll", 756, 520, 8, 0),
                           ("scroll", 756, 520, 8, 0)]


def test_a_click_that_switches_the_view_doesnt_steer_the_next_scroll():
    chrome = mail()                                                          # the folder list isn't a mapped panel
    chrome.scroll_areas = chrome.scroll_areas[:1]
    chrome.controls += [Control("Promotions", "link", 100, 300, 150, 30), Control("Primary", "tab", 700, 100, 120, 30)]
    native = mail(app="Mail", window="Inbox - 12 messages")                 # the mailboxes are their own panel
    native.scroll_areas.append(Control("Mailboxes", "scroll area", 110, 520, 220, 880))
    native.texts.append(Control("Inbox", "text", 60, 200))
    for context, clicks in ((chrome, ["Promotions"]), (chrome, ["Invoice 1", "Primary"]), (native, ["Inbox"])):
        engine, host = hands(context, observe=lambda: None)
        for label in clicks:
            run(engine.handle("click", {"text": label}))
        run(engine.handle("scroll", {"direction": "down"}))
        assert wheel(host) == [("scroll", 756, 520, 8, 0)], clicks          # the content: the page


def test_the_last_click_stops_counting_once_they_move_the_pointer_or_leave_that_page():
    inbox = "https://mail.example.com/#inbox"

    def first_wheel(now, mouse=None, before=None):
        engine, host = hands(before or mail(url=inbox), observe=lambda: None)
        run(engine.handle("click", {"id": 2}))                               # an email in the list, at 1300,200
        host.mouse = mouse
        engine.ctx.screen = ([shot()], now)                                  # what's on screen when they say it
        run(engine.handle("scroll", {"direction": "up"}))
        return wheel(host)[0][1:3]
    assert first_wheel(mail(url=inbox + "/FMfcg123")) == (1300, 520)        # opened from the list: still there
    assert first_wheel(mail(url=inbox), mouse=(1300, 200)) == (1300, 520)   # the pointer's where plip left it
    assert first_wheel(mail(url=inbox), mouse=(600, 700)) == (600, 700)     # they moved it: that's the hint now
    assert first_wheel(mail(url="https://mail.example.com/#sent")) == (756, 520)      # another folder
    assert first_wheel(mail(url="https://news.example.com/")) == (756, 520)           # another site
    assert first_wheel(mail(url=inbox, app="Safari")) == (756, 520)                   # another app
    assert first_wheel(mail(url=inbox, frame=Rect(0, 0, 1000, 700))) == (756, 520)   # not in the window anymore
    unread = mail(window="Inbox - 12 messages")                                        # no address: the title
    assert first_wheel(mail(window="Inbox - 11 messages"), before=unread) == (1300, 520)
    assert first_wheel(mail(window="Sent - 4 messages"), before=unread) == (756, 520)


def test_when_nothing_moves_it_tries_the_scroll_bar_more_spots_and_page_keys_then_says_what_it_tried(quick):
    host = FakeHost()
    host.has_bar, host.focus_role, host.focused_area = True, "AXWebArea", Rect(0, 80, 1512, 880)
    engine, _ = hands(mail(), host=host, observe=lambda: mail())             # nothing ever moves
    run(engine.handle("click", {"id": 2}))
    out = run(engine.handle("scroll", {"direction": "up"}))
    assert host.calls[1:] == [("scroll", 1300, 520, -8, 0), ("scroll_bar", 1300, 520, "up", False),
                              ("scroll", 756, 520, -8, 0), ("press", "pageup")]
    assert out.result.report == (
        "scrolled up, but nothing moved. tried the wheel at the 'Email' panel, its scroll bar, the wheel at the "
        "focused panel and pressing pageup: it's at the top, or that part doesn't scroll. give the panel's x,y from "
        "scrollable or the id of anything inside it, or read_page for the whole text")
    assert "scrolled" not in engine.ctx.state
    # The map can't see that panel move, the pixels there can: the page key did it, and it says so.
    engine.ctx.fingerprint = lambda x, y: bytes([40 * sum(call[0] == "press" for call in host.calls)]) * 2560
    out = run(engine.handle("scroll", {"direction": "up"}))
    assert out.result.report == ("scrolled up (the wheel at the 'Email' panel, its scroll bar and the wheel at the "
                                 "focused panel did nothing; pressing pageup did)")
    assert engine.ctx.state["scrolled"] is True


def test_a_toolbar_that_shows_on_hover_or_a_ticking_clock_isnt_a_scroll(quick):
    host = FakeHost()
    host.focus_role = "AXWebArea"

    def hovered():
        context = mail()
        context.texts.append(Control(f"10:4{len(host.calls)}", "text", 1450, 40))     # ticks every step
        x, y = host.pointed[-1]                                             # the row under the pointer shows its
        context.controls += [Control("Archive", "button", x + 100, y), Control("Delete", "button", x + 140, y)]
        return context
    engine, _ = hands(mail(), host=host, observe=hovered)
    lit = lambda x, y: bytes([90 if (round(x), round(y)) == host.pointed[-1] else 0]) * 2560     # noqa: E731
    engine.ctx.fingerprint = lit                                             # own buttons, and lights up
    run(engine.handle("click", {"id": 2}))
    out = run(engine.handle("scroll", {"direction": "up"}))
    assert out.result.report.startswith("scrolled up, but nothing moved. tried the wheel at the 'Email' panel, the "
                                        "wheel at the page and pressing pageup")
    assert "scrolled" not in engine.ctx.state


def test_the_fallbacks_stay_quick_when_nothing_moves(monkeypatch):
    from mcp_vision.buddy.actions import control

    slept, looks = [], []
    monkeypatch.setattr(control.time, "sleep", slept.append)
    host = FakeHost()
    host.has_bar, host.focus_role, host.focused_area = True, "AXWebArea", Rect(400, 140, 600, 760)
    engine, _ = hands(mail(), host=host, observe=lambda: looks.append(1) or mail())
    engine.ctx.fingerprint = lambda x, y: bytes(2560)                       # a still screen
    run(engine.handle("click", {"id": 2}))
    run(engine.handle("scroll", {"direction": "up"}))
    assert [call[0] for call in host.calls[1:]] == ["scroll", "scroll_bar", "scroll", "press"]   # each got a go
    # the click's 0.25 s glide (they see where it acts) + the scroll fallbacks' own ~1.1 s; was ~3.7 s, 17 reads
    assert sum(slept) <= 0.25 + 1.2 and len(looks) <= 5


def test_a_panel_already_at_the_bottom_isnt_pushed_anywhere_else(quick):
    host = FakeHost()
    host.has_bar, host.focus_role = True, "AXWebArea"
    bottom = cart_page()
    bottom.scroll_areas = [Control("page (scrolled to the bottom)", "scroll area", 756, 520, 1512, 880)]
    engine, _ = hands(cart_page(), host=host, observe=lambda: bottom)
    out = run(engine.handle("scroll", {"direction": "down"}))
    assert host.calls == [("scroll", 756, 520, 8, 0)]
    assert "nothing moved: the page is already at the bottom" in out.result.report


def test_scroll_all_is_honest_when_it_never_moved_or_never_stopped(quick):
    host = FakeHost()
    host.has_bar, host.focus_role = True, "AXWebArea"
    engine, _ = hands(cart_page(), host=host, observe=lambda: cart_page())
    out = run(engine.handle("scroll", {"direction": "down", "amount": "all"}))
    assert "nothing moved" in out.result.report and "scrolled to the bottom" not in out.result.report
    assert host.calls == [("scroll", 756, 520, 60, 0), ("scroll_bar", 756, 520, "down", True), ("press", "end")]
    host = FakeHost()
    engine, _ = hands(cart_page(), host=host, observe=Site(host).snapshot)   # a feed that never ends
    out = run(engine.handle("scroll", {"direction": "down", "amount": "all"}))
    assert "still going" in out.result.report and len(wheel(host)) == 10


def test_page_keys_only_go_where_they_scroll(quick):
    def keys(role):
        host = FakeHost()
        host.focus_role = role
        engine, _ = hands(cart_page(), host=host, observe=lambda: cart_page())
        run(engine.handle("scroll", {"direction": "down"}))
        run(engine.handle("scroll", {"direction": "up", "amount": "all"}))
        run(engine.handle("scroll_to", {"text": "Pricing", "direction": "down"}))
        return [call[1] for call in host.calls if call[0] == "press"]
    for role in ("AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXSlider", "AXIncrementor",
                 "AXPopUpButton", "AXMenuButton", "AXList", None):          # typing, changing a value, or can't tell
        assert keys(role) == [], role
    assert keys("AXWebArea") == keys("") == ["pagedown", "home", "pagedown"]


def test_scroll_to_has_the_app_bring_it_into_view_and_checks_it_did(quick):
    from mcp_vision.buddy.actions.host import Reveal

    host = FakeHost()
    host.revealed = Reveal(found=True, asked=True, scroller=(1300, 520), direction="down")

    def shown():
        context = mail()
        if ("scroll_to_visible", "Q3 invoice") in host.calls:
            context.texts.append(Control("Re: Q3 invoice", "text", 1300, 600))
        return context
    glides = []
    engine, _ = hands(mail(), host=host, observe=shown, animate=lambda x, y, label: glides.append(label) or 0.0)
    out = run(engine.handle("scroll_to", {"text": "Q3 invoice"}))
    assert out.result.report == "found 'Re: Q3 invoice' and scrolled it into view; it's on screen now"
    assert host.calls == [("scroll_to_visible", "Q3 invoice")] and glides == ["Re: Q3 invoice"]
    # Nothing came into view: the wheel at the panel holding it, the way it is now (a slow app went past it).
    host = FakeHost()
    host.revealed = Reveal(found=True, asked=True, scroller=(1300, 520), direction="down")
    host.revealed_now = Reveal(found=True, scroller=(1300, 520), direction="up")

    def wheeled():
        context = mail(len(wheel(host)))
        if len(wheel(host)) >= 2:
            context.texts.append(Control("Re: Q3 invoice", "text", 1300, 300))
        return context
    engine, _ = hands(mail(), host=host, observe=wheeled)
    out = run(engine.handle("scroll_to", {"text": "Q3 invoice"}))
    assert wheel(host) == [("scroll", 1300, 520, -8, 0)] * 2 and "after 2 scrolls" in out.result.report
    # In view but past what the map lists: it says so instead of wheeling away from it.
    host = FakeHost()
    host.revealed = Reveal(found=True, asked=True, scroller=(1300, 520), direction="down")
    host.revealed_now = Reveal(found=True, scroller=(1300, 520), at=(1300, 610))
    engine, _ = hands(mail(), host=host, observe=lambda: mail())
    out = run(engine.handle("scroll_to", {"text": "Q3 invoice"}))
    assert out.result.report == "found 'Q3 invoice' and scrolled it into view; it's on screen now" and not wheel(host)
    assert engine.ctx.state["scrolled"] is True


def test_scroll_to_tells_never_moved_from_reached_the_end(quick):
    engine, _ = hands(cart_page(), observe=lambda: cart_page())             # nothing ever moves
    out = run(engine.handle("scroll_to", {"text": "Pricing"}))
    assert out.result.report.startswith("'Pricing' isn't on screen and nothing moved: tried the wheel at the page.")
    host = FakeHost()
    engine, _ = hands(host=host, observe=Site(host, end=1).snapshot)          # moved, then hit the bottom
    out = run(engine.handle("scroll_to", {"text": "Pricing", "direction": "down"}))
    assert "scrolled to the end" in out.result.report


