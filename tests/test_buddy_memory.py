"""Plip's memory: the store, every importer (against real sqlite files), AI-memory paste, and use in turns."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import closing

import pytest

from buddy_fakes import Capturer, Events, FakeHost, Pointer, ScriptedBrain, Speaker
from mcp_vision.buddy.actions import ActionContext, ActionEngine
from mcp_vision.buddy.companion import Companion
from mcp_vision.buddy.factory import make_notes
from mcp_vision.buddy.memory import Memory, looks_sensitive, mask
from mcp_vision.buddy.memory.importers import (
    MEMORY_PROMPT, browser_databases, import_autofill,
    import_contacts, parse_ai_memory, parse_tab_lines, split_address,
)


@pytest.fixture
def memory(tmp_path):
    return Memory(tmp_path / "memory.json")


# -- the store ----------------------------------------------------------------------------

def test_memory_dedupes_ranks_sources_and_persists_privately(memory, tmp_path):
    memory.add("email", "Hussain@Example.com", "autofill")
    memory.add("email", "hussain@example.com ", "contacts")              # same email, second source
    memory.add("email", "work@company.com", "mail")
    memory.add("phone", "+1 (555) 010-2000", "contacts")
    memory.add("phone", "+15550102000", "imessage")                         # same number, formatted differently
    memory.add("address.city", "Brooklyn", "chatgpt")
    memory.add("address.city", "New York", "contacts")                      # contacts beats chatgpt
    memory.add("favorite_airline", "Delta", "chatgpt")                      # unknown key -> note
    assert [fact.sources for fact in memory.facts if fact.key == "email"][0] == ["autofill", "contacts"]
    assert len([fact for fact in memory.facts if fact.key == "phone"]) == 1
    assert memory.best("address.city").value == "New York"
    assert memory.values("email") == ["Hussain@Example.com", "work@company.com"]
    assert memory.best("note").value == "favorite airline: Delta"
    memory.save()
    assert oct(os.stat(tmp_path / "memory.json").st_mode & 0o777) == "0o600"
    again = Memory(tmp_path / "memory.json")
    assert len(again.facts) == len(memory.facts) and again.best("address.city").value == "New York"


def test_sensitive_facts_are_masked_and_kept_out_of_prompts(memory):
    assert looks_sensitive("note", "passport: X1234567") and looks_sensitive("note", "card 4111 1111 1111 1111")
    assert looks_sensitive("note", "ssn is 123-45-6789") and not looks_sensitive("phone", "+1 555 010 2000")
    assert not looks_sensitive("note", "order 1234567890123")                  # long number but fails Luhn
    memory.add("name.full", "Hussain Syed", "contacts")
    memory.add("note", "passport number: X1234567", "you")
    memory.add("note", "prefers aisle seats", "you")
    summary = memory.summary()
    assert "X1234567" not in summary and "(sensitive, ask before using) passport number" in summary
    assert "- name: Hussain Syed" in summary and "- prefers aisle seats" in summary
    card = next(fact.card() for fact in memory.facts if fact.sensitive)
    assert card["value"] == mask("passport number: X1234567") == "•••• 4567" and card["sources"] == ["You told Plip"]


def test_profile_derives_first_and_last_names(memory):
    memory.add("name.full", "Hussain Syed", "you")
    assert memory.profile()["name.first"] == "Hussain" and memory.profile()["name.last"] == "Syed"
    other = Memory(memory.path.with_name("m2.json"))
    other.add("name.first", "Ada", "contacts")
    other.add("name.last", "Lovelace", "contacts")
    assert other.profile()["name.full"] == "Ada Lovelace"
    assert Memory(memory.path.with_name("empty.json")).summary() == ""


def test_merge_and_forget_a_source(memory):
    added = memory.merge("chatgpt", [("name.full", "Hussain"), ("note", "likes ramen"), ("note", "likes ramen")])
    assert added == 2 and memory.imports["chatgpt"]["count"] == 3
    memory.add("name.full", "Hussain", "contacts")
    assert memory.forget_source("chatgpt") == 1                     # the name survives via Contacts
    assert [fact.value for fact in memory.facts] == ["Hussain"] and "chatgpt" not in memory.imports


# -- Contacts & Mail ---------------------------------------------------------------------------

CONTACTS_OUTPUT = ("name.first\tHussain\nname.last\tSyed\ncompany\tPlip Labs\ntitle\tFounder\n"
                   "email\thussain@example.com\nphone\t+1 555 010 2000\naddress.street\t1 Main St\n"
                   "address.city\tBrooklyn\naddress.state\tNY\naddress.postal\t11201\naddress.country\tUSA\n"
                   "birthday\t1999-3-7\nsocial.LinkedIn\thussain-syed\nsocial.Mastodon\t@h@social\n"
                   "website\tmissing value\njunk line\n")


def test_contacts_card_parses_every_field():
    facts = parse_tab_lines(CONTACTS_OUTPUT)
    assert ("birthday", "1999-03-07") in facts and ("linkedin", "hussain-syed") in facts
    assert ("note", "mastodon: @h@social") in facts and ("address.postal", "11201") in facts
    assert not any(value == "missing value" for _, value in facts)
    facts, error = import_contacts(FakeHost(osa_reply=CONTACTS_OUTPUT))
    assert error == "" and ("company", "Plip Labs") in facts


def test_contacts_permission_errors_are_explained():
    class Denied(FakeHost):
        def osascript(self, script, timeout=10.0):
            raise RuntimeError("execution error: Not authorized to send Apple events to Contacts. (-1743)")
    facts, error = import_contacts(Denied())
    assert facts == [] and "Automation" in error


# -- browser autofill ---------------------------------------------------------------------------

def make_web_data(path, *, modern=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        if modern:
            db.execute("CREATE TABLE addresses (guid TEXT, use_count INT)")
            db.execute("CREATE TABLE address_type_tokens (guid TEXT, type INT, value TEXT, verification_status INT)")
            db.executemany("INSERT INTO address_type_tokens VALUES ('g', ?, ?, 0)", [
                (3, "Hussain"), (5, "Syed"), (7, "Hussain Syed"), (9, "hussain@example.com"), (14, "+15550102000"),
                (77, "1 Main St"), (33, "Brooklyn"), (34, "NY"), (35, "11201"), (36, "US"), (60, "Plip Labs"),
                (9, "not-an-email"), (999, "ignored")])
        else:
            db.execute("CREATE TABLE autofill_profiles (guid TEXT, company_name TEXT, street_address TEXT, city TEXT, "
                       "state TEXT, zipcode TEXT, country_code TEXT)")
            db.execute("INSERT INTO autofill_profiles VALUES ('g', 'Old Co', '9 Elm St', 'Austin', 'TX', '73301', 'US')")
            db.execute("CREATE TABLE autofill_profile_names (guid TEXT, first_name TEXT, middle_name TEXT, "
                       "last_name TEXT, full_name TEXT)")
            db.execute("INSERT INTO autofill_profile_names VALUES ('g', 'Ada', '', 'Lovelace', 'Ada Lovelace')")
            db.execute("CREATE TABLE autofill_profile_emails (guid TEXT, email TEXT)")
            db.execute("INSERT INTO autofill_profile_emails VALUES ('g', 'ada@example.com')")
            db.execute("CREATE TABLE autofill_profile_phones (guid TEXT, number TEXT)")
            db.execute("INSERT INTO autofill_profile_phones VALUES ('g', '+1 512 555 0100')")
        db.execute("CREATE TABLE autofill (name TEXT, value TEXT, count INT)")
        db.executemany("INSERT INTO autofill VALUES (?, ?, ?)", [
            ("email", "typed@example.com", 6), ("firstName", "Hussain", 3), ("tel", "555-0100-22", 2),
            ("email", "once@example.com", 1), ("search", "pizza", 40), ("postal_code", "11201", 4)])
        db.commit()


def test_autofill_reads_modern_and_legacy_chrome_schemas(tmp_path):
    home = tmp_path / "home"
    make_web_data(home / "Library/Application Support/Google/Chrome/Default/Web Data")
    make_web_data(home / "Library/Application Support/Arc/User Data/Profile 1/Web Data", modern=False)
    assert len(browser_databases(str(home))) == 2
    facts, error = import_autofill(str(home))
    assert error == ""
    for fact in [("name.full", "Hussain Syed"), ("email", "hussain@example.com"), ("phone", "+15550102000"),
                 ("address.street", "1 Main St"), ("company", "Plip Labs"), ("name.full", "Ada Lovelace"),
                 ("address.city", "Austin"), ("email", "ada@example.com"), ("email", "typed@example.com"),
                 ("name.first", "Hussain"), ("address.postal", "11201")]:
        assert fact in facts, fact
    values = [value for _, value in facts]
    assert "not-an-email" not in values and "once@example.com" not in values and "pizza" not in values
    assert len(facts) == len({(key, value.casefold()) for key, value in facts})        # deduped


def test_autofill_without_a_browser():
    facts, error = import_autofill("/nonexistent/home")
    assert facts == [] and "No Chrome" in error


# -- AI memory --------------------------------------------------------------------------------

CHATGPT_OUTPUT = """Here's what I know about you:
- **Name:** Hussain Syed
- Email: hussain@example.com
- Location: Brooklyn
- Home address: 1 Main St, Brooklyn, NY 11201, USA
- Job title: Founder
- Company: Plip Labs
- Person: Sara, sister
- Prefers aisle seats on flights
- Diet: vegetarian
1. GitHub: hussainn7
"""


def test_ai_memory_paste_maps_known_keys_and_keeps_the_rest_as_notes():
    facts = parse_ai_memory(CHATGPT_OUTPUT)
    assert ("name.full", "Hussain Syed") in facts and ("email", "hussain@example.com") in facts
    assert ("address.city", "Brooklyn") in facts and ("address.state", "NY") in facts
    assert ("address.postal", "11201") in facts and ("address.country", "USA") in facts
    assert ("title", "Founder") in facts and ("github", "hussainn7") in facts
    assert ("note", "Person: Sara, sister") in facts and ("note", "Prefers aisle seats on flights") in facts
    assert ("note", "Diet: vegetarian") in facts
    assert not any("Here's what" in value for _, value in facts)
    assert split_address("somewhere in Paris") == [("address.street", "somewhere in Paris")]
    assert "key: value" in MEMORY_PROMPT and "no markdown" in MEMORY_PROMPT


# -- memory in action ----------------------------------------------------------------------------

def test_remember_and_forget_actions(memory):
    engine = ActionEngine(ActionContext(host=FakeHost(), memory=memory))
    done = asyncio.run(engine.handle("remember", {"fact": "my seat preference is aisle"}))
    assert done.status == "done" and memory.best("note").value == "my seat preference is aisle"
    private = asyncio.run(engine.handle("remember", {"fact": "passport number: X1234567"}))
    assert "keep that private" in private.result.say
    assert asyncio.run(engine.handle("remember", {"fact": "email: new@example.com"})).status == "done"
    assert "new@example.com" in memory.values("email")
    gone = asyncio.run(engine.handle("forget", {"about": "seat preference"}))
    assert gone.status == "done" and all("seat" not in fact.value for fact in memory.facts)
    assert asyncio.run(engine.handle("forget", {"about": "dragons"})).status == "failed"
    assert json.loads(memory.path.read_text())["facts"]


def test_companion_turns_carry_what_plip_knows(memory):
    memory.add("name.full", "Hussain Syed", "contacts")
    memory.add("note", "vegetarian", "chatgpt")
    brain = ScriptedBrain("Hi Hussain.")
    buddy = Companion(brain=brain, capturer=Capturer(), speaker=Speaker(), pointer=Pointer(), observer=Events(),
                      notes=make_notes(memory))
    asyncio.run(buddy.respond("what should I eat tonight"))
    text = brain.calls[0][-1].text
    assert "about the user" in text and "- name: Hussain Syed" in text and "- vegetarian" in text
    assert text.index("about the user") < text.index("the user said: what should I eat tonight")
