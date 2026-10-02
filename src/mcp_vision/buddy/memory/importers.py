"""Bring your details into Plip from the places they already live.

Every importer returns ``(facts, error)`` where facts are ``(key, value)``
pairs. Nothing leaves the Mac: files are read locally and AppleScript asks
apps on this machine.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

Facts = list[tuple[str, str]]

# -- Contacts "My Card" ---------------------------------------------------------------------

CONTACTS_SCRIPT = r'''
on addLine(theKey, theValue)
    global out
    try
        if theValue is not missing value and (theValue as text) is not "" then
            set out to out & theKey & tab & (theValue as text) & linefeed
        end if
    end try
end addLine

global out
set out to ""
tell application "Contacts"
    set c to my card
    if c is missing value then return ""
    try
        my addLine("name.first", first name of c)
    end try
    try
        my addLine("name.last", last name of c)
    end try
    try
        my addLine("company", organization of c)
    end try
    try
        my addLine("title", job title of c)
    end try
    try
        repeat with e in emails of c
            my addLine("email", value of e)
        end repeat
    end try
    try
        repeat with p in phones of c
            my addLine("phone", value of p)
        end repeat
    end try
    try
        repeat with a in addresses of c
            my addLine("address.street", street of a)
            my addLine("address.city", city of a)
            my addLine("address.state", state of a)
            my addLine("address.postal", zip of a)
            my addLine("address.country", country of a)
        end repeat
    end try
    try
        set b to birth date of c
        if b is not missing value then
            my addLine("birthday", ((year of b) as text) & "-" & ((month of b as integer) as text) & "-" & ((day of b) as text))
        end if
    end try
    try
        repeat with u in urls of c
            my addLine("website", value of u)
        end repeat
    end try
    try
        repeat with s in social profiles of c
            my addLine("social." & (service name of s), user name of s)
        end repeat
    end try
end tell
return out
'''

SOCIAL_KEYS = {"linkedin": "linkedin", "twitter": "twitter", "x": "twitter", "github": "github"}


def parse_tab_lines(text: str) -> Facts:
    facts: Facts = []
    for line in text.splitlines():
        if "\t" not in line:
            continue
        key, value = (part.strip() for part in line.split("\t", 1))
        if not value or value == "missing value":
            continue
        if key.startswith("social."):
            service = key.split(".", 1)[1].lower()
            key = SOCIAL_KEYS.get(service, "note")
            if key == "note":
                value = f"{service}: {value}"
        if key == "birthday":
            match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
            if match:
                value = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        facts.append((key, value))
    return facts


def import_contacts(host) -> tuple[Facts, str]:
    try:
        return parse_tab_lines(host.osascript(CONTACTS_SCRIPT, timeout=20)), ""
    except Exception as exc:
        return [], _why(exc, "Contacts")


# -- Mail accounts ---------------------------------------------------------------------------------

MAIL_SCRIPT = r'''
set out to ""
tell application "Mail"
    repeat with a in every account
        try
            set out to out & "name.full" & tab & (full name of a) & linefeed
        end try
        try
            repeat with e in (email addresses of a)
                set out to out & "email" & tab & e & linefeed
            end repeat
        end try
    end repeat
end tell
return out
'''


def import_mail(host) -> tuple[Facts, str]:
    try:
        return parse_tab_lines(host.osascript(MAIL_SCRIPT, timeout=20)), ""
    except Exception as exc:
        return [], _why(exc, "Mail")


# -- Chromium-family autofill (Chrome, Arc, Brave, Edge, Chromium) ----------------------------------

BROWSER_DIRS = (
    "Library/Application Support/Google/Chrome", "Library/Application Support/Arc/User Data",
    "Library/Application Support/BraveSoftware/Brave-Browser", "Library/Application Support/Microsoft Edge",
    "Library/Application Support/Chromium", "Library/Application Support/Vivaldi",
    ".config/google-chrome", ".config/chromium", ".config/BraveSoftware/Brave-Browser",
)
# Chromium's autofill FieldType ids used in *_type_tokens tables.
FIELD_TYPES = {3: "name.first", 5: "name.last", 7: "name.full", 9: "email", 14: "phone", 10: "phone",
               30: "address.street", 77: "address.street", 33: "address.city", 34: "address.state",
               35: "address.postal", 36: "address.country", 60: "company"}
FORM_FIELDS = (
    (re.compile(r"e-?mail", re.I), "email"),
    (re.compile(r"first.?name|given.?name|fname", re.I), "name.first"),
    (re.compile(r"last.?name|family.?name|surname|lname", re.I), "name.last"),
    (re.compile(r"^(full.?)?name$", re.I), "name.full"),
    (re.compile(r"phone|tel\b|mobile|cell", re.I), "phone"),
    (re.compile(r"zip|postal", re.I), "address.postal"),
    (re.compile(r"^city$|town", re.I), "address.city"),
    (re.compile(r"company|organi[sz]ation|employer", re.I), "company"),
)
VALIDATORS = {
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I),
    "phone": re.compile(r"^\+?[\d\s().-]{7,20}$"),
    "address.postal": re.compile(r"^[A-Za-z0-9 -]{3,10}$"),
}


def browser_databases(home: str) -> list[Path]:
    found = []
    for base in BROWSER_DIRS:
        for path in glob.glob(os.path.join(home, base, "*", "Web Data")):
            found.append(Path(path))
    return found


def _valid(key: str, value: str) -> bool:
    pattern = VALIDATORS.get(key)
    return bool(value) and len(value) < 120 and (pattern is None or bool(pattern.match(value)))


def read_autofill(db_path: Path) -> Facts:
    facts: Facts = []
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "web-data.sqlite"
        shutil.copy2(db_path, copy)                    # the browser keeps the live file locked
        with closing(sqlite3.connect(f"file:{copy}?mode=ro", uri=True)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in sorted(name for name in tables if name.endswith("type_tokens")):
                try:
                    for field_type, value in db.execute(f'SELECT type, value FROM "{table}"'):
                        key = FIELD_TYPES.get(int(field_type))
                        if key and _valid(key, str(value or "").strip()):
                            facts.append((key, str(value).strip()))
                except sqlite3.Error:
                    continue
            if "autofill_profile_names" in tables:
                for first, last, full in db.execute(
                        "SELECT first_name, last_name, full_name FROM autofill_profile_names"):
                    for key, value in (("name.first", first), ("name.last", last), ("name.full", full)):
                        if value:
                            facts.append((key, str(value).strip()))
            if "autofill_profile_emails" in tables:
                facts += [("email", email) for (email,) in db.execute("SELECT email FROM autofill_profile_emails")
                          if email and _valid("email", email)]
            if "autofill_profile_phones" in tables:
                facts += [("phone", number) for (number,) in db.execute("SELECT number FROM autofill_profile_phones")
                          if number and _valid("phone", number)]
            if "autofill_profiles" in tables:
                columns = {row[1] for row in db.execute("PRAGMA table_info(autofill_profiles)")}
                wanted = [("company_name", "company"), ("street_address", "address.street"), ("city", "address.city"),
                          ("state", "address.state"), ("zipcode", "address.postal"), ("country_code", "address.country")]
                present = [(column, key) for column, key in wanted if column in columns]
                if present:
                    query = "SELECT " + ", ".join(column for column, _ in present) + " FROM autofill_profiles"
                    for row in db.execute(query):
                        for (_, key), value in zip(present, row, strict=False):
                            if value:
                                facts.append((key, str(value).strip()))
            if "autofill" in tables:                     # form history: only values typed more than once
                for name, value, count in db.execute(
                        "SELECT name, value, count FROM autofill WHERE count >= 2 ORDER BY count DESC LIMIT 400"):
                    for pattern, key in FORM_FIELDS:
                        if pattern.search(str(name or "")) and _valid(key, str(value or "").strip()):
                            facts.append((key, str(value).strip()))
                            break
    return facts


def import_autofill(home: str) -> tuple[Facts, str]:
    databases = browser_databases(home)
    if not databases:
        return [], "No Chrome, Arc, Brave or Edge profile found."
    facts: Facts = []
    errors = []
    for path in databases:
        try:
            facts += read_autofill(path)
        except (OSError, sqlite3.Error) as exc:
            errors.append(f"{path.parent.parent.name}: {exc}")
    seen, unique = set(), []
    for fact in facts:
        marker = (fact[0], fact[1].casefold())
        if marker not in seen:
            seen.add(marker)
            unique.append(fact)
    return unique, ("; ".join(errors) if errors and not unique else "")


# -- AI memory (ChatGPT, Claude, Gemini) ------------------------------------------------------------------

MEMORY_PROMPT = (
    "I'm setting up Plip, my AI assistant on my Mac, and want it to know what you know about me. "
    "List everything you've saved in memory or learned about me, one fact per line, as `key: value`. "
    "Include my name, email, phone, home address, birthday, where I work and my role, school, important "
    "people (as `person: name, relationship`), preferences (food, travel, airline seats, brands), "
    "ongoing projects, tools I use, and anything else useful. Plain text only: no intro, no commentary, "
    "no markdown."
)
AI_KEYS = (
    (re.compile(r"^(my |full )?name$"), "name.full"),
    (re.compile(r"^first name|^given name"), "name.first"),
    (re.compile(r"^(last name|surname|family name)"), "name.last"),
    (re.compile(r"e-?mail"), "email"),
    (re.compile(r"phone|mobile|cell"), "phone"),
    (re.compile(r"^(home |street |mailing )?address$"), "address"),
    (re.compile(r"^(city|location|lives in|based in|hometown|home city|current location)$"), "address.city"),
    (re.compile(r"^country"), "address.country"),
    (re.compile(r"birthday|date of birth|^dob$|^born"), "birthday"),
    (re.compile(r"^(company|employer|works at|workplace|organi[sz]ation|work)$"), "company"),
    (re.compile(r"^(job|role|title|job title|occupation|position|profession)$"), "title"),
    (re.compile(r"^(website|site|portfolio|homepage)$"), "website"),
    (re.compile(r"linkedin"), "linkedin"),
    (re.compile(r"github"), "github"),
    (re.compile(r"^(twitter|x)( handle)?$"), "twitter"),
    (re.compile(r"school|university|college|education|alma mater"), "school"),
)
US_ADDRESS_RE = re.compile(r"^(?P<street>.+?),\s*(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)"
                           r"(?:,\s*(?P<country>.+))?$")


def split_address(value: str) -> Facts:
    match = US_ADDRESS_RE.match(value.strip())
    if not match:
        return [("address.street", value)]
    facts = [("address.street", match.group("street")), ("address.city", match.group("city").strip()),
             ("address.state", match.group("state")), ("address.postal", match.group("zip"))]
    if match.group("country"):
        facts.append(("address.country", match.group("country").strip()))
    return facts


def parse_ai_memory(text: str) -> Facts:
    facts: Facts = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•·]|\d+[.)])\s*", "", raw).strip().strip("`").strip()
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        if len(line) < 3 or line.endswith(":") or line.lower().startswith(("here's", "here is", "sure", "i don't")):
            continue
        if ":" not in line:
            facts.append(("note", line))
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if not value:
            continue
        lowered = key.lower()
        mapped = next((target for pattern, target in AI_KEYS if pattern.search(lowered)), None)
        if mapped == "address":
            facts += split_address(value)
        elif mapped:
            facts.append((mapped, value))
        else:
            facts.append(("note", f"{key}: {value}"))
    return facts


# -- iMessage -------------------------------------------------------------------------------------

APPLE_EPOCH = 978307200


def chat_db(home: str) -> Path:
    return Path(home) / "Library" / "Messages" / "chat.db"


def strip_handle(value: str) -> str:
    value = (value or "").strip()
    return value[2:] if re.match(r"^[epEP]:", value) else value


def open_chat_db(path: Path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)


def own_handles(db) -> list[str]:
    handles: list[str] = []
    for query in ("SELECT DISTINCT account FROM message WHERE is_from_me = 1 AND account IS NOT NULL",
                  "SELECT DISTINCT account_login FROM chat WHERE account_login IS NOT NULL"):
        try:
            for (value,) in db.execute(query):
                handle = strip_handle(str(value))
                if handle and handle not in handles and ("@" in handle or re.search(r"\d{7,}", handle)):
                    handles.append(handle)
        except sqlite3.Error:
            continue
    return handles


def frequent_contacts(db, days: int = 120, limit: int = 15, now: float | None = None) -> list[dict]:
    import time

    cutoff = ((now or time.time()) - APPLE_EPOCH - days * 86400) * 1e9
    rows = db.execute(
        "SELECT h.id, COUNT(*) AS n FROM message m JOIN handle h ON m.handle_id = h.ROWID "
        "WHERE m.date > ? GROUP BY h.id ORDER BY n DESC LIMIT ?", (cutoff, limit)).fetchall()
    return [{"handle": handle, "count": int(count)} for handle, count in rows]


def import_imessage(home: str) -> tuple[Facts, list[str], list[dict], str]:
    """Returns (facts, own handles, frequent contacts, error)."""
    path = chat_db(home)
    if not path.exists():
        return [], [], [], "No Messages history on this Mac."
    try:
        with closing(open_chat_db(path)) as db:
            handles = own_handles(db)
            contacts = frequent_contacts(db)
    except (sqlite3.Error, OSError) as exc:
        if "authoriz" in str(exc).lower() or "unable to open" in str(exc).lower() or isinstance(exc, PermissionError):
            return [], [], [], "Needs Full Disk Access (System Settings → Privacy & Security)."
        return [], [], [], f"Couldn't read Messages: {exc}"
    facts = [("email" if "@" in handle else "phone", handle) for handle in handles]
    return facts, handles, contacts, ""


def decode_attributed_body(blob: bytes | None) -> str:
    """The text inside a Messages ``attributedBody`` (an NSAttributedString typedstream)."""
    if not blob:
        return ""
    try:
        data = bytes(blob)
        start = data.index(b"NSString") + len(b"NSString")
        plus = data.index(b"+", start)                # typedstream marks the string with '+'
        position = plus + 1
        length = data[position]
        position += 1
        if length == 0x81:
            length = int.from_bytes(data[position:position + 2], "little")
            position += 2
        elif length == 0x82:
            length = int.from_bytes(data[position:position + 4], "little")
            position += 4
        return data[position:position + length].decode("utf-8", errors="replace")
    except (ValueError, IndexError):
        return ""


def _why(exc: Exception, app: str) -> str:
    text = str(exc).lower()
    if "not authorized" in text or "-1743" in text or "not allowed" in text:
        return f"Allow Plip to control {app} (System Settings → Privacy & Security → Automation)."
    if "macos" in text:
        return str(exc)
    return f"Couldn't read {app}."
