"""Form filling and iMessage: snapping to real fields, previews, confirmations, contact lookup, sending."""
from __future__ import annotations

import asyncio

from buddy_fakes import Capturer, Events, FakeHost, Pointer, ScriptedBrain, Speaker, shot
from mcp_vision.buddy.actions import ActionContext, ActionEngine
from mcp_vision.buddy.companion import Companion
from mcp_vision.buddy.forms import resolve_fields
from mcp_vision.buddy.memory import Memory
from mcp_vision.buddy.messages import parse_people, resolve_recipient, send_script
from mcp_vision.buddy.screen_context import Control, ScreenContext

FORM = ScreenContext(app="Safari", window="Sign up", controls=[
    Control("First name", "text field", 500, 300), Control("Last name", "text field", 500, 360),
    Control("Email", "text field", 500, 420), Control("Sign up", "button", 500, 600)])


def px(gx, gy, image=shot()):
    x, y = image.from_global(gx, gy)
    return round(x), round(y)


def ctx_with_form(host=None, memory=None):
    return ActionContext(host=host or FakeHost(), memory=memory, screen=([shot()], FORM))


def test_fields_snap_onto_real_text_fields():
    fx, fy = px(500, 300)
    lx, ly = px(500, 360)
    fields = resolve_fields(ctx_with_form(), [
        {"x": fx + 9, "y": fy - 6, "label": "", "value": "Hussain"},        # a little off: snaps
        {"x": lx, "y": ly, "label": "Surname", "value": "Syed"},
        {"x": 1000, "y": 700, "label": "Nickname", "value": "H"},           # far from any field: kept as given
        {"x": fx, "y": fy, "label": "Empty", "value": "  "},                # nothing to type: skipped
        {"label": "no coordinates", "value": "x"}, "junk"])
    assert [(round(f["x"]), round(f["y"]), f["label"], f["value"]) for f in fields[:2]] == [
        (500, 300, "First name", "Hussain"), (500, 360, "Surname", "Syed")]
    assert fields[2]["label"] == "Nickname" and len(fields) == 3


def test_fill_form_previews_then_types_each_field_after_yes():
    host = FakeHost()
    engine = ActionEngine(ctx_with_form(host))
    fx, fy = px(500, 300)
    ex, ey = px(500, 420)
    outcome = asyncio.run(engine.handle("fill_form", {"fields": [
        {"x": fx, "y": fy, "label": "First name", "value": "Hussain"},
        {"x": ex, "y": ey, "label": "Email", "value": "hussain@example.com"}]}))
    assert outcome.status == "pending" and outcome.preview.title == "Fill 2 fields"
    assert outcome.preview.lines == ["First name → Hussain", "Email → hussain@example.com"]
    assert host.calls == []                                              # nothing typed yet
    done = asyncio.run(engine.answer(True))
    assert host.calls == [("field", 500, 300, "Hussain"), ("field", 500, 420, "hussain@example.com")]
    assert done.result.say == "Filled in 2 fields. Give it a quick look before you submit."


def test_fill_form_needs_a_screen_and_real_values():
    engine = ActionEngine(ActionContext(host=FakeHost()))
    assert "look at the form first" in asyncio.run(engine.handle("fill_form", {"fields": [{"x": 1}]})).message
    engine = ActionEngine(ctx_with_form())
    empty = asyncio.run(engine.handle("fill_form", {"fields": [{"x": 1, "y": 2, "value": ""}]}))
    assert empty.message == "I didn't find any fields I could fill with what I know about you."


def test_form_turn_end_to_end(tmp_path):
    memory = Memory(tmp_path / "memory.json")
    memory.add("name.full", "Hussain Syed", "contacts")
    host = FakeHost()
    fx, fy = px(500, 300)
    lx, ly = px(500, 360)
    brain = ScriptedBrain('I can fill your name in. [DO:fill_form {"fields": ['
                          f'{{"x": {fx}, "y": {fy}, "label": "First name", "value": "Hussain"}}, '
                          f'{{"x": {lx}, "y": {ly}, "label": "Last name", "value": "Syed"}}]}}] Should I go ahead?')

    class FormContext:
        def snapshot(self):
            return FORM
    events, speaker = Events(), Speaker()
    buddy = Companion(brain=brain, capturer=Capturer(), speaker=speaker, pointer=Pointer(), observer=events,
                      context=FormContext(), actions=ActionEngine(ActionContext(host=host, memory=memory)),
                      walkthroughs=False)
    asyncio.run(buddy.respond("fill this out for me"))
    confirm = events.of("confirm")[0]
    assert confirm["title"] == "Fill 2 fields" and confirm["confirm"] == "Fill it in"
    assert "First name | text field" in brain.calls[0][-1].text
    asyncio.run(buddy.respond("yep"))
    assert host.calls == [("field", 500, 300, "Hussain"), ("field", 500, 360, "Syed")]
    assert speaker.said[-1].startswith("Filled in 2 fields")


# -- messages --------------------------------------------------------------------------------

CONTACTS = ("Mom\t_$!<Home>!$_=+1 555 010 1111,_$!<Mobile>!$_=+1 555 010 2222,\tmom@example.com,\n"
            "Momo Tanaka\t\tmomo@example.com,\n"
            "No Handles\t\t\n")


class ContactsHost(FakeHost):
    def __init__(self, reply=CONTACTS, fail=None):
        super().__init__()
        self.reply, self.fail = reply, fail

    def osascript(self, script, timeout=10.0):
        self.calls.append(("osascript", script))
        if self.fail:
            raise RuntimeError(self.fail)
        return self.reply if 'tell application "Contacts"' in script else ""


def test_people_parsing_prefers_mobile_numbers():
    people = parse_people(CONTACTS)
    assert people == [{"name": "Mom", "handle": "+1 555 010 2222"}, {"name": "Momo Tanaka", "handle": "momo@example.com"}]


def test_recipients_resolve_from_numbers_contacts_and_memory(tmp_path):
    assert resolve_recipient(ActionContext(host=FakeHost()), "+1 (555) 010-3333") == (
        "+1 (555) 010-3333", "+1 (555) 010-3333", [])
    assert resolve_recipient(ActionContext(host=FakeHost()), "sara@example.com")[1] == "sara@example.com"
    name, handle, others = resolve_recipient(ActionContext(host=ContactsHost()), "mom")
    assert (name, handle, others) == ("Mom", "+1 555 010 2222", ["Momo Tanaka"])
    memory = Memory(tmp_path / "m.json")
    memory.contacts = [{"name": "Sara", "handle": "+15550104444", "count": 9}]
    assert resolve_recipient(ActionContext(host=FakeHost(), memory=memory), "sara")[1] == "+15550104444"


def test_message_errors_are_friendly():
    engine = ActionEngine(ActionContext(host=ContactsHost(reply="")))
    assert asyncio.run(engine.handle("send_message", {"to": "Zed", "text": "hi"})).message == \
        "I couldn't find Zed in your contacts."
    denied = ActionEngine(ActionContext(host=ContactsHost(fail="Not authorized to send Apple events (-1743)")))
    assert asyncio.run(denied.handle("send_message", {"to": "Mom", "text": "hi"})).message == \
        "Allow Plip to use Contacts, then ask again."
    assert asyncio.run(engine.handle("send_message", {"to": "Mom"})).message == "What should the message say?"
    assert asyncio.run(engine.handle("send_message", {"text": "hi"})).message == "Who should I send it to?"


def test_send_message_waits_for_yes_then_sends_safely():
    host = ContactsHost()
    engine = ActionEngine(ActionContext(host=host))
    outcome = asyncio.run(engine.handle("send_message", {"to": "Mom", "text": 'On my way "soon"!'}))
    assert outcome.status == "pending" and outcome.preview.title == "Send to Mom"
    assert outcome.preview.lines == ['“On my way "soon"!”', "to +1 555 010 2222", "also matched: Momo Tanaka"]
    assert not any('tell application "Messages"' in call[1] for call in host.calls)
    sent = asyncio.run(engine.answer(True))
    script = host.calls[-1][1]
    assert sent.result.say == "Sent to Mom." and 'tell application "Messages"' in script
    assert 'send "On my way \\"soon\\"!" to participant "+1 555 010 2222" of svc' in script
    assert send_script("x", 'a"b\\c').count('a\\"b\\\\c') == 2                     # escaped in both branches
