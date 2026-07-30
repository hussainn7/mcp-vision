"""
Unified tool registry for the multi-specialist agent.

Every tool is one entry in REGISTRY:
    name -> {"fn": callable, "schema": ollama-tool-schema,
             "verify": callable|None, "dangerous": bool}

- fn         does the work, returns a short string.
- schema     the function schema handed to Ollama (tool-calling).
- verify     optional eval gate: verify(**args) -> "" if the action landed,
             else a short problem string (reused from mac_agent).
- dangerous  True => the orchestrator pauses for terminal approval first.

Nothing here talks to the model except guide_user; the orchestrator (agent.py)
picks tools per specialist and runs the loop.
"""

import ollama

from config import cfg
from mac_agent import (
    SCHEMAS as MAC_SCHEMAS,
    VERIFY as MAC_VERIFY,
    add_reminder,
    create_event,
    create_note,
    list_dir,
    open_app,
    read_file,
)
from phase2_mcp import playwright_tools as pt

MAC_FNS = {
    "create_note": create_note,
    "add_reminder": add_reminder,
    "create_event": create_event,
    "open_app": open_app,
    "list_dir": list_dir,
    "read_file": read_file,
}
_MAC_SCHEMA_BY_NAME = {s["function"]["name"]: s for s in MAC_SCHEMAS}


# --- browser tools (thin wrappers over phase2_mcp, DOM/role based) -----------
# All require Chrome started with --remote-debugging-port=9222; if it's not up
# the wrappers return an "ERROR: ..." string the model can react to.

def web_navigate(url):
    return pt.navigate(url)


def web_read():
    return pt.get_page_text()


def web_click_text(text):
    return pt.click_element_by_text(text)


def web_click_role(role, name=""):
    return pt.click_element_by_role(role, name or None)


def web_type(text):
    return pt.type_text(text)


def web_press(key):
    # ponytail: Enter here can submit a form; gate as dangerous if that bites.
    return pt.press_key(key)


def web_scroll(direction="down", clicks=3):
    return pt.scroll(direction, clicks)


# --- guide mode: no tool exists, so tell the human the next move -------------

GUIDE_SYSTEM = """You look at a macOS screenshot and tell the user the single
next physical action to take toward their goal: which button to click, what to
type, or which menu to open. One concrete step, plain language, no preamble.
If the goal already looks complete, say so."""


def guide_user(goal):
    from phase1_vision.capture import capture_screen

    img, _ = capture_screen(save=False)
    if img.width > cfg.inference_width:
        w = cfg.inference_width
        img = img.resize((w, int(img.height * w / img.width)))
    buf = cfg.output_dir / "_guide.png"
    img.save(buf)
    reply = ollama.chat(
        model=cfg.model,
        messages=[
            {"role": "system", "content": GUIDE_SYSTEM},
            {"role": "user",
             "content": f"Goal: {goal}\nWhat is the single next thing I should do?",
             "images": [str(buf)]},
        ],
        keep_alive=cfg.ollama_keep_alive,
        options={"temperature": 0, "num_predict": 150},
    )
    return "GUIDE: " + reply["message"]["content"].strip()


def _fn(name, description, required, props):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "required": required, "properties": props}}}


_STR = {"type": "string"}

WEB_SCHEMAS = {
    "web_navigate": _fn("web_navigate", "Open a URL in the attached Chrome.", ["url"], {"url": _STR}),
    "web_read": _fn("web_read", "Read the visible text of the current page.", [], {}),
    "web_click_text": _fn("web_click_text", "Click the element containing this visible text.", ["text"], {"text": _STR}),
    "web_click_role": _fn("web_click_role", "Click by ARIA role, e.g. role='button' name='Submit'.", ["role"], {"role": _STR, "name": _STR}),
    "web_type": _fn("web_type", "Type text into the focused field.", ["text"], {"text": _STR}),
    "web_press": _fn("web_press", "Press a key like 'Enter' or 'Tab'.", ["key"], {"key": _STR}),
    "web_scroll": _fn("web_scroll", "Scroll the page.", [], {"direction": _STR, "clicks": {"type": "integer"}}),
    "guide_user": _fn("guide_user", "When no tool can act, look at the screen and tell the user the next step.", ["goal"], {"goal": _STR}),
}

WEB_FNS = {
    "web_navigate": web_navigate, "web_read": web_read,
    "web_click_text": web_click_text, "web_click_role": web_click_role,
    "web_type": web_type, "web_press": web_press, "web_scroll": web_scroll,
    "guide_user": guide_user,
}

# irreversible / outbound actions only — these pause for approval
DANGEROUS = {"web_click_text", "web_click_role"}


def _entry(name, fn, schema):
    return {"fn": fn, "schema": schema, "verify": MAC_VERIFY.get(name), "dangerous": name in DANGEROUS}


REGISTRY = {}
for _n, _f in MAC_FNS.items():
    REGISTRY[_n] = _entry(_n, _f, _MAC_SCHEMA_BY_NAME[_n])
for _n, _f in WEB_FNS.items():
    REGISTRY[_n] = _entry(_n, _f, WEB_SCHEMAS[_n])


def SCHEMAS_FOR(names):
    """Ollama tool schemas for an allowlist, silently dropping unknown names."""
    return [REGISTRY[n]["schema"] for n in names if n in REGISTRY]


def demo():
    assert set(MAC_FNS) <= set(REGISTRY)
    assert "guide_user" in REGISTRY and not REGISTRY["guide_user"]["dangerous"]
    assert REGISTRY["web_click_text"]["dangerous"]
    assert REGISTRY["create_note"]["verify"] is not None       # gate carried over
    assert REGISTRY["web_read"]["verify"] is None
    got = [s["function"]["name"] for s in SCHEMAS_FOR(["create_note", "web_read", "nope"])]
    assert got == ["create_note", "web_read"]                  # unknown dropped
    print("ok")


if __name__ == "__main__":
    demo()
