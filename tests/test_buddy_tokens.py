"""Lean history: a finished multi-step request folds into one exchange."""
from __future__ import annotations

import asyncio

from buddy_fakes import Capturer, FakeHost, Pointer, ScriptedBrain, Speaker
from mcp_vision.buddy.actions import ActionContext, ActionEngine
from mcp_vision.buddy.actions.host import FileHit
from mcp_vision.buddy.companion import Companion
from mcp_vision.buddy.conversation import Conversation


def test_fold_keeps_the_request_and_what_plip_said_and_drops_the_steps():
    talk = Conversation()
    talk.record("what's the time in tokyo", "It's 9pm there.")
    talk.record("find my lease", "Looking. (did: Searching files for lease)")
    talk.record("now: (action results) 1. Lease.pdf in ~/Documents (2,000 words of page text…)",
                "Found it, opening it. (did: Opening the file)", step=True)
    assert len(talk.history()) == 6 and talk.requests == 2
    talk.fold()
    texts = [turn.text for turn in talk.history()]
    assert texts == ["what's the time in tokyo", "It's 9pm there.", "find my lease",
                     "Looking. (did: Searching files for lease) Found it, opening it. (did: Opening the file)"]
    talk.fold()                                        # a single exchange has nothing to fold
    assert len(talk.history()) == 4


def test_a_finished_task_leaves_one_exchange_and_the_next_call_is_lighter(tmp_path):
    host = FakeHost(home=str(tmp_path))
    host.files = [FileHit(str(tmp_path / "Documents/Apartment/Lease-2026.pdf"), 1759370000.0)]
    brain = ScriptedBrain('Looking. [DO:search_files {"query": "lease"}]', "Found it in Apartment.", "You're welcome.")
    plip = Companion(brain=brain, capturer=Capturer(), speaker=Speaker(), pointer=Pointer(), walkthroughs=False,
                     actions=ActionEngine(ActionContext(host=host, state={})))
    assert asyncio.run(plip.respond("find my lease")).turns == 2
    history = plip.conversation.history()
    assert [turn.role for turn in history] == ["user", "assistant"] and history[0].text == "find my lease"
    assert "Found it in Apartment." in history[1].text and "Lease-2026.pdf" not in history[1].text
    asyncio.run(plip.respond("thanks"))
    sent = brain.calls[-1]                             # the third model call: thanks, with the folded history
    assert not any("action results" in turn.text for turn in sent[:-1])
