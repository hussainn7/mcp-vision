"""Nothing that buys, sends, deletes or runs things gets around its confirm card, however it's aimed."""
from __future__ import annotations

import asyncio

from buddy_fakes import Capturer, FakeHost, ScriptedBrain, Speaker, shot
from mcp_vision.buddy.actions import ActionContext, ActionEngine
from mcp_vision.buddy.companion import Companion
from mcp_vision.buddy.screen_context import Control, ScreenContext


def run(coro):
    return asyncio.run(coro)


def checkout():
    return ScreenContext(app="Safari", window="Checkout", controls=[
        Control("Place your order", "button", 700, 600, 400, 40),
        Control("Gift note", "text field", 600, 300, 300, 28),
        Control("Search products", "search field", 600, 60, 300, 28),
        Control("Message", "text field", 600, 800, 500, 60),
        Control("Trash", "dock item", 1400, 950, 50, 50)])


def hands(context=None, host=None):
    host = host or FakeHost()
    context = context or checkout()
    shots = [shot()]
    context.describe(shots)
    engine = ActionEngine(ActionContext(host=host, screen=(shots, context)))
    engine.ctx.observe = lambda: context
    return engine, host


def test_typing_never_clicks_a_buy_button_and_return_in_a_message_box_asks_first():
    engine, host = hands()
    out = run(engine.handle("type_text", {"text": "x", "id": 1, "submit": True}))       # [1] is "Place your order"
    assert out.status == "failed" and "button" in out.message and "click" not in [c[0] for c in host.calls]
    out = run(engine.handle("type_text", {"text": "on my way", "id": 4, "submit": True}))  # the message box
    assert out.status == "pending" and out.preview.title == "Send “on my way” in Message" and host.calls == []
    out = run(engine.handle("type_text", {"text": "usb-c hub", "id": 3, "submit": True}))  # a search: no card
    assert out.status == "done" and host.calls[-1] == ("press", "return")


def test_a_click_by_x_y_anywhere_on_a_wide_buy_button_asks_first():
    engine, host = hands()
    # 160 pt left of the button's center (pixels at the 1512/1280 screenshot scale), still inside it
    out = run(engine.handle("click", {"x": round((700 - 160) / 1.18125), "y": round(600 / 1.18125), "label": "ok"}))
    assert out.status == "pending" and out.preview.title == "Click “Place your order”" and host.calls == []


def test_keys_that_quit_delete_log_out_or_send_ask_first_however_theyre_spelled():
    for keys in ("cmd+q", "command+q", "⌘+q", "shift+cmd+delete", "cmd+return", "cmd+shift+d", "cmd+shift+q"):
        engine, host = hands()
        assert run(engine.handle("press", {"keys": keys})).status == "pending", keys
    engine, host = hands()
    assert run(engine.handle("press", {"keys": "cmd+t"})).status == "done"


def test_more_ways_of_saying_buy_or_pay_ask_first():
    from mcp_vision.buddy.actions.control import RISKY

    for label in ("Order now", "Complete order", "Proceed to payment", "Continue to checkout", "Make payment",
                  "Donate", "Place bid", "Withdraw", "Authorize", "Grant access", "Subscribe"):
        assert RISKY.search(label), label
    for label in ("Apply filters", "Orders", "Allow notifications later"):
        assert not RISKY.search(label), label


def test_dragging_to_the_trash_and_opening_an_app_or_script_ask_first(tmp_path):
    engine, host = hands()
    engine.ctx.screen[1].controls.append(Control("Report.pdf", "dock item", 300, 950, 50, 50))
    engine.ctx.screen[1].describe([shot()])
    out = run(engine.handle("drag", {"from_id": 6, "to_id": 5}))
    assert out.status == "pending" and "Trash" in out.preview.title and host.calls == []
    home = tmp_path / "me"
    (home / "Downloads").mkdir(parents=True)
    (home / "Downloads" / "setup.command").write_text("echo hi")
    (home / "Downloads" / "notes.pdf").write_text("%PDF")
    engine, host = hands(host=FakeHost(home=str(home)))
    assert run(engine.handle("open_file", {"path": str(home / "Downloads" / "setup.command")})).status == "pending"
    assert run(engine.handle("open_file", {"path": str(home / "Downloads" / "notes.pdf")})).status == "done"


def test_what_a_reply_does_after_a_card_or_a_failed_click_isnt_run():
    host = FakeHost()
    page = checkout()

    class Map:
        def snapshot(self):
            return page

    def plip(reply):
        brain = ScriptedBrain(reply, "okay.")
        return Companion(brain=brain, capturer=Capturer(), context=Map(), speaker=Speaker(),
                         actions=ActionEngine(ActionContext(host=host)), settle_interval=0.01)

    result = run(plip('placing it. [DO:click {"id": 1}] [DO:press {"keys": "return"}]').respond("buy it"))
    assert result.pending == "Click “Place your order”" and host.calls == []          # nothing ran behind the card
    result = run(plip('replying. [DO:click {"id": 9}] [DO:type_text {"text": "yes"}]').respond("reply yes"))
    assert ("type", "yes") not in host.calls                                          # the click failed: no typing
