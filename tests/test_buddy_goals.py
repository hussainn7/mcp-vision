"""Tasks that take several steps: [GOAL]…[DONE], failures handed back, [DONE] checked, x,y clicks guarded."""
from __future__ import annotations

import asyncio

from buddy_fakes import Capturer, FakeHost, ScriptedBrain, Speaker
from mcp_vision.buddy.actions import ActionContext, ActionEngine
from mcp_vision.buddy.companion import CONTINUE_RE, Companion
from mcp_vision.buddy.pointing import GoalTag, ReplyStream, SpeechChunk
from mcp_vision.buddy.router import rule_route
from mcp_vision.buddy.screen_context import Control, ScreenContext


class Spotify(FakeHost):
    """Opening Spotify shows its window; clicking Play (where it is) starts the music; Buy does nothing."""

    def __init__(self, play_works=True):
        super().__init__()
        self.open_now, self.playing, self.play_works = False, False, play_works

    def open_app(self, path):
        super().open_app(path)
        self.open_now = True

    def click(self, x, y, button="left", count=1):
        self.calls.append(("click", round(x), round(y)))
        if self.play_works and (round(x), round(y)) == (300, 200):
            self.playing = True

    def snapshot(self):
        if not self.open_now:
            return ScreenContext(app="Finder", controls=[Control("New Folder", "button", 50, 50)])
        controls = [Control("Discover Weekly", "link", 300, 150), Control("Play", "button", 300, 200),
                    Control("Buy now", "button", 600, 400)]
        if self.playing:
            controls.append(Control("Pause", "button", 300, 260))
        return ScreenContext(app="Spotify", window="Discover Weekly", controls=controls)


def buddy(host, *replies, **kw):
    brain = ScriptedBrain(*replies)
    speaker = Speaker()
    companion = Companion(brain=brain, capturer=Capturer(), context=host, speaker=speaker,
                          actions=ActionEngine(ActionContext(host=host)), settle_interval=0.01, **kw)
    return companion, brain, speaker


def test_a_goal_keeps_going_after_the_open_until_done():
    host = Spotify()
    companion, brain, _ = buddy(host,
                                '[GOAL: play discover weekly] opening spotify. [DO:open_app {"name": "spotify"}]',
                                'hitting play. [DO:click {"id": 2}] playing it now. [DONE]')
    result = asyncio.run(companion.respond("open spotify and play my discover weekly"))
    # It stopped after the open before: nothing told it to look again.
    assert host.playing and result.finished and result.turns == 2 and result.outcome == "done"
    step = brain.calls[1][-1].text
    assert "(step 1 toward: play discover weekly" in step and "open_app: done" in step
    assert result.goal == "play discover weekly" and companion._goal == ""          # finished: the task is closed


def test_a_failed_step_goes_back_to_the_model_with_a_hint_and_isnt_said_twice():
    host = Spotify()
    host.open_now = True
    companion, brain, speaker = buddy(host,
                                      '[GOAL: play it] [DO:click {"id": 9}] playing it now.',
                                      'trying the play button by its name. [DO:click {"text": "Play"}] there. [DONE]')
    result = asyncio.run(companion.respond("play discover weekly"))
    step = brain.calls[1][-1].text
    assert "click failed: I can't find number 9 on screen anymore" in step
    assert "aim by its text or by x,y from the screenshot instead" in step              # for the model only
    assert host.playing and result.finished
    assert not any("number 9" in line or "playing it now" in line for line in speaker.said)   # muted, not lied


def test_done_after_a_click_that_changed_nothing_is_sent_back():
    host = Spotify(play_works=False)
    host.open_now = True
    companion, brain, _ = buddy(host,
                                '[GOAL: play it] [DO:click {"id": 2}] playing. [DONE]',
                                "that play button isn't responding, so it's not playing yet.")
    result = asyncio.run(companion.respond("play discover weekly"))
    assert "you ended with [DONE] right after clicking, but nothing on screen changed" in brain.calls[1][-1].text
    assert not result.finished and result.turns == 2


def test_a_click_by_x_y_on_buy_still_asks_first():
    host = Spotify()
    host.open_now = True
    companion, _, _ = buddy(host, '[GOAL: buy it] [DO:click {"x": 508, "y": 339}] buying it.')   # Buy now, by pixels
    result = asyncio.run(companion.respond("buy it"))
    assert result.pending == "Click “Buy now”" and not any(call[0] == "click" for call in host.calls)


def test_goal_tags_are_events_never_spoken_and_routing_spots_tasks():
    stream = ReplyStream()
    events = stream.feed("[GOAL: find remote jobs on indeed] pulling them up. ") + stream.close()
    assert events[0] == GoalTag("find remote jobs on indeed") and stream.goal == "find remote jobs on indeed"
    assert [e.text for e in events if isinstance(e, SpeechChunk)] == ["pulling them up."]
    assert rule_route("open spotify and play my discover weekly").multistep
    assert rule_route("find me some backend jobs on jobs.example").multistep
    assert not rule_route("open spotify").multistep and not rule_route("what's the capital of france").multistep
    assert CONTINUE_RE.match("okay keep going please") and not CONTINUE_RE.match("go on linkedin and find jobs")


def test_a_clock_on_the_page_doesnt_stop_it_settling():
    one = ScreenContext(app="Mail", controls=[Control("Inbox", "link", 40, 80), Control("2 min ago", "text", 300, 80)])
    later = ScreenContext(app="Mail", controls=[Control("Inbox", "link", 41, 80), Control("3 min ago", "text", 300, 80)])
    assert one.signature() != later.signature() and one.settle_signature() == later.settle_signature()


def test_the_map_carries_the_page_its_text_and_where_it_scrolls():
    from mcp_vision.buddy.geometry import Rect, ScreenInfo, Screenshot

    shot = Screenshot(screen=ScreenInfo(1, Rect(0, 0, 1512, 982), is_cursor_screen=True), data=b"", width=1280,
                      height=831)
    context = ScreenContext(app="Chrome", window="Deals", url="https://shop.example/deals",
                            controls=[Control("Add to cart", "button", 300, 200), Control("File", "menu", 115, 16)],
                            texts=[Control("Today's deals", "text", 300, 120), Control("Add to cart", "text", 300, 200),
                                   Control("Soundcore speaker $29", "text", 300, 160)],
                            scroll_areas=[Control("page (scrolled 40% down)", "page", 700, 500)])
    text = context.describe([shot])
    assert "page: https://shop.example/deals" in text and "[1] Add to cart | button | 254,169" in text
    assert "menu bar (click by name): File 97,14" in text and "[2]" not in text            # menus get no ids
    assert "visible text: Today's deals · Soundcore speaker $29" in text                   # not "Add to cart" twice
    assert "note: the page is scrolled down" in text and "  screen1:" not in text          # one screen: no headings
    assert context.find("soundcore") is context.texts[2] and context.find("add to cart").role == "button"


def test_read_page_reads_the_whole_page_or_just_what_its_after():
    host = Spotify()
    engine = ActionEngine(ActionContext(host=host))
    engine.ctx.read = lambda: ["Deals", "1. Apple AirTag $24", "2. Soundcore speaker $29", "Shipping: free over $35"]
    whole = asyncio.run(engine.handle("read_page", {})).result.report
    assert whole.startswith("page text:\nDeals\n1. Apple AirTag $24")
    found = asyncio.run(engine.handle("read_page", {"find": "soundcore"})).result.report
    assert "Soundcore speaker $29" in found and "Shipping" in found and "page text about 'soundcore'" in found


def test_task_steps_skip_the_screenshot_when_the_map_has_it_and_look_brings_it_back():
    class Shop(Spotify):
        def snapshot(self):                     # a rich page: plenty of controls
            return ScreenContext(app="Safari", controls=[Control(f"item {n}", "link", 100, 40 * n) for n in range(1, 10)])

    host = Shop()
    host.open_now = True
    companion, brain, _ = buddy(host,
                                '[GOAL: add it] [DO:click {"id": 1}] adding it.',
                                'let me see it. [DO:look {}]',
                                'it is in. [DONE]')
    asyncio.run(companion.respond("add item one to my cart"))
    first, step, looked = (turns[-1] for turns in brain.calls)
    assert first.images                                                    # the request itself always sees it
    assert not step.images and "screen unchanged since your last look" in step.text   # ~1,400 tokens saved
    assert looked.images                                                   # asked to look: it gets the pixels


def test_routine_steps_think_less_and_anything_that_went_wrong_keeps_their_depth():
    class Brain(ScriptedBrain):
        def __init__(self, *replies):
            super().__init__(*replies)
            self.efforts = []

        async def stream(self, *, system, turns, detailed=False, effort=None):
            self.efforts.append(effort)
            async for chunk in super().stream(system=system, turns=turns, detailed=detailed):
                yield chunk

    host = Spotify()
    brain = Brain('[GOAL: play it] opening it. [DO:open_app {"name": "spotify"}]',
                  'picking the playlist. [DO:click {"id": 9}]',
                  'by its name then. [DO:click {"text": "Play"}] playing. [DONE]')
    companion = Companion(brain=brain, capturer=Capturer(), context=host, speaker=Speaker(),
                          actions=ActionEngine(ActionContext(host=host)), settle_interval=0.01)
    result = asyncio.run(companion.respond("open spotify and play discover weekly"))
    # the ask: their depth · after a clean open that moved the screen: low · after a failed click: their depth
    assert brain.efforts == [None, "low", None] and result.finished and host.playing


def test_a_yes_to_plips_own_question_is_the_confirmation_but_never_for_money():
    from mcp_vision.buddy.actions import Consent, Preview

    asked = "Want me to send it?"
    assert Consent.given("yes", asked).covers(Preview(title="Send to Sara", confirm="Send"))
    assert Consent.given("yeah go ahead", "I'm about to delete the old ones. Should I?").covers(
        Preview(title="Click “Delete”", confirm="Click it"))
    assert Consent.given("send it", "") is None                                    # their own request: its one card
    assert Consent.given("yes but wait till tomorrow", asked) is None                # a yes with a hold isn't one
    assert Consent.given("should I?", asked) is None
    paying = Consent.given("yes", "Want me to check out?")
    assert paying is None or not paying.covers(Preview(title="Click “Proceed to checkout”", confirm="Click it"))
    once = Consent.given("yes", asked)
    assert once.covers(Preview(title="Send to Sara")) and not once.covers(Preview(title="Send to Sam"))   # one step
    assert not Consent.given("yes", asked).covers(Preview(title="Send to Sara", firm=True))   # the card asks itself


def test_the_card_doesnt_ask_again_after_they_said_yes_in_words():
    host = Spotify()
    host.open_now = True
    host.snapshot = lambda: ScreenContext(app="Mail", controls=[Control("Send", "button", 600, 400)])
    companion, brain, speaker = buddy(host, "it's ready. want me to send it?", 'sending it. [DO:click {"id": 1}]')
    first = asyncio.run(companion.respond("reply to sara that i'm in"))
    assert first.spoken.endswith("want me to send it?") and not first.pending
    second = asyncio.run(companion.respond("yes"))
    assert not second.pending and ("click", 600, 400) in host.calls                 # no second "are you sure"
    companion, _, _ = buddy(host, 'sending it. [DO:click {"id": 1}]')
    assert asyncio.run(companion.respond("send it")).pending == "Click “Send”"        # asked in their words: one card


def test_a_tool_call_written_out_is_never_read_aloud_and_the_model_hears_why_once():
    def said(*chunks):
        stream = ReplyStream()
        events = [event for chunk in chunks for event in stream.feed(chunk)] + stream.close()
        return [e.text for e in events if isinstance(e, SpeechChunk)], stream.leaked

    assert said("Sure, let me save it. <inv", 'oke name="Bash"><parameter name="command">cat > ~/x.txt</parameter>',
                "</invoke> Saved it!") == (["Sure, let me save it."], True)
    assert said("<thinking>they want the cart</thinking>Adding it now.") == (["Adding it now."], False)
    assert said("if a < b, the loop stops early. ") == (["if a < b, the loop stops early."], False)
    host = Spotify()
    host.open_now = True
    companion, brain, speaker = buddy(host, 'on it. <function_calls><invoke name="click">', "clicking play. [DONE]")
    asyncio.run(companion.respond("play it"))
    assert "you have no shell, terminal or file tools" in brain.calls[1][-1].text
    assert not any("invoke" in line for line in speaker.said)


def test_a_long_answer_reads_about_a_minute_aloud_and_leaves_the_rest_on_screen():
    long = " ".join(f"Point number {n} is about something worth reading on screen." for n in range(40))
    companion, _, speaker = buddy(Spotify(), long)
    result = asyncio.run(companion.respond("tell me everything"))
    spoken = "".join(line for line in speaker.said if line != "The rest is on screen.")
    assert len(spoken) <= 900 and speaker.said[-1] == "The rest is on screen."
    assert "Point number 39" in result.spoken                                   # still all in the answer text
