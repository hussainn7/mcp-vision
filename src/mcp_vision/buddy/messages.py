"""iMessage: find the person, show the message, send it after your OK."""
from __future__ import annotations

import re

from mcp_vision.buddy.actions.base import ActionContext, ActionError, ActionResult, ActionSpec, Preview
from mcp_vision.buddy.actions.host import applescript_string

PHONE_RE = re.compile(r"^\+?[\d\s().-]{7,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.IGNORECASE)


def find_script(name: str) -> str:
    quoted = applescript_string(name)
    return f'''
set out to ""
tell application "Contacts"
    set found to (every person whose name contains {quoted} or nickname contains {quoted})
    repeat with p in found
        set out to out & (name of p) & tab
        repeat with ph in phones of p
            set out to out & (label of ph) & "=" & (value of ph) & ","
        end repeat
        set out to out & tab
        repeat with e in emails of p
            set out to out & (value of e) & ","
        end repeat
        set out to out & linefeed
    end repeat
end tell
return out
'''


def parse_people(text: str) -> list[dict]:
    """Lines of ``name<TAB>label=phone,...<TAB>email,...`` -> people with a best handle."""
    people = []
    for line in text.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0].strip():
            continue
        name = parts[0].strip()
        phones = []
        for item in (parts[1] if len(parts) > 1 else "").split(","):
            if "=" in item:
                label, number = item.split("=", 1)
                if number.strip():
                    phones.append((label.lower(), number.strip()))
        emails = [item.strip() for item in (parts[2] if len(parts) > 2 else "").split(",") if item.strip()]
        ranked = sorted(phones, key=lambda pair: 0 if ("iphone" in pair[0] or "mobile" in pair[0]) else 1)
        handle = ranked[0][1] if ranked else (emails[0] if emails else "")
        if handle:
            people.append({"name": name, "handle": handle})
    return people


def resolve_recipient(ctx: ActionContext, to: str) -> tuple[str, str, list[str]]:
    """(display name, handle, other matches) for a name, number or email."""
    to = to.strip()
    if EMAIL_RE.match(to) or (PHONE_RE.match(to) and sum(char.isdigit() for char in to) >= 7):
        return to, to, []
    memory = ctx.memory
    if memory is not None:
        for contact in getattr(memory, "contacts", []):
            if contact.get("name") and contact["name"].lower() == to.lower():
                return contact["name"], contact["handle"], []
    try:
        people = parse_people(ctx.host.osascript(find_script(to), timeout=15))
    except Exception as exc:
        text = str(exc).lower()
        if "-1743" in text or "not authorized" in text:
            raise ActionError("Allow Plip to use Contacts, then ask again.") from exc
        raise ActionError(f"I couldn't look up {to} in your contacts.") from exc
    if not people:
        raise ActionError(f"I couldn't find {to} in your contacts.")
    exact = [person for person in people if person["name"].lower() == to.lower()]
    best = (exact or people)[0]
    others = [person["name"] for person in people if person is not best][:3]
    return best["name"], best["handle"], others


def send_script(handle: str, text: str) -> str:
    return f'''
tell application "Messages"
    try
        set svc to 1st account whose service type = iMessage
        send {applescript_string(text)} to participant {applescript_string(handle)} of svc
    on error
        set svc to 1st account whose service type = SMS
        send {applescript_string(text)} to participant {applescript_string(handle)} of svc
    end try
end tell
'''


def send_imessage(host, handle: str, text: str) -> None:
    host.osascript(send_script(handle, text), timeout=20)


def preview_message(ctx: ActionContext, args: dict) -> Preview:
    to = str(args.get("to") or "").strip()
    text = str(args.get("text") or "").strip()
    if not to:
        raise ActionError("Who should I send it to?")
    if not text:
        raise ActionError("What should the message say?")
    if len(text) > 2000:
        raise ActionError("That message is too long for me to send.")
    name, handle, others = resolve_recipient(ctx, to)
    lines = [f"“{text}”", f"to {handle}" if handle != name else "via iMessage"]
    if others:
        lines.append("also matched: " + ", ".join(others))
    return Preview(title=f"Send to {name}", lines=lines, confirm="Send",
                   state={"name": name, "handle": handle, "text": text})


def send_message(ctx: ActionContext, args: dict, state: dict | None = None) -> ActionResult:
    if state is None:
        preview = preview_message(ctx, args)
        state = preview.state
    send_imessage(ctx.host, state["handle"], state["text"])
    return ActionResult(say=f"Sent to {state['name']}.", detail=f"sent to {state['name']}")


SPECS = (
    ActionSpec("send_message", "messages", "Message to {to}", send_message, preview=preview_message,
               args='{"to", "text"}'),
)
