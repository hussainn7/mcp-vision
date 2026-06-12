"""
The actual tool implementations for the MCP server.

These functions do the real work - looking up coordinates in the latest
elements JSON and sending the appropriate pyautogui commands. The MCP
server in server.py just wraps these with the @mcp.tool() decorator.
"""

import json
import time
from pathlib import Path

import pyautogui
from loguru import logger

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import cfg

# pyautogui safety settings
# failsafe means moving your mouse to a corner will abort the script.
# keep this on during development - you'll thank yourself later.
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05  # tiny delay between actions, helps with reliability


def _get_latest_elements() -> list[dict]:
    """
    Find and load the most recently written elements JSON from the output dir.

    This is how the click/type tools know what's on screen - they read whatever
    OmniParser last wrote. The orchestrator keeps this fresh by running
    OmniParser at the start of each cycle.
    """
    json_files = sorted(cfg.output_dir.glob("elements_*.json"), reverse=True)
    if not json_files:
        raise FileNotFoundError(
            f"No elements JSON found in {cfg.output_dir}. "
            f"Run parse_screen.py first."
        )

    latest = json_files[0]
    logger.debug(f"Loading elements from: {latest.name}")
    with open(latest) as f:
        return json.load(f)


def _find_element(element_id: int) -> dict:
    """Look up a specific element by its numbered ID."""
    elements = _get_latest_elements()
    for elem in elements:
        if elem["id"] == element_id:
            return elem
    available = [e["id"] for e in elements]
    raise ValueError(
        f"Element {element_id} not found. "
        f"Available IDs: {available}"
    )


def click_element(element_id: int, double: bool = False) -> str:
    """
    Click the screen element with the given ID.

    Looks up the (x, y) center coordinates from the latest OmniParser results
    and sends a mouse click there.

    Args:
        element_id: the number shown in the annotated screenshot
        double: if True, sends a double-click instead

    Returns:
        a confirmation string describing what was clicked
    """
    elem = _find_element(element_id)
    x, y = elem["x"], elem["y"]
    label = elem.get("label", "unknown element")

    logger.info(f"Clicking element {element_id} '{label}' at ({x}, {y})")

    # smooth move so it doesn't teleport - easier to see what's happening
    pyautogui.moveTo(x, y, duration=0.15)
    if double:
        pyautogui.doubleClick(x, y)
    else:
        pyautogui.click(x, y)

    time.sleep(0.1)  # give the UI a moment to respond before the next action
    return f"Clicked element {element_id} ('{label}') at ({x}, {y})"


def right_click_element(element_id: int) -> str:
    """
    Right-click an element. Useful for context menus.

    Args:
        element_id: the number shown in the annotated screenshot

    Returns:
        a confirmation string
    """
    elem = _find_element(element_id)
    x, y = elem["x"], elem["y"]
    label = elem.get("label", "unknown element")

    logger.info(f"Right-clicking element {element_id} '{label}' at ({x}, {y})")
    pyautogui.moveTo(x, y, duration=0.15)
    pyautogui.rightClick(x, y)
    time.sleep(0.1)
    return f"Right-clicked element {element_id} ('{label}') at ({x}, {y})"


def type_code(text: str, interval: float = 0.02) -> str:
    """
    Type a string at the current cursor position.

    Uses pyautogui.write() for printable ASCII characters. For anything that
    needs special handling (newlines, tabs, keyboard shortcuts), the text
    goes through pyautogui.hotkey() or press() on a character-by-character
    basis.

    Args:
        text: the string to type
        interval: seconds between each keystroke. slower = more reliable
                  on laggy apps. 0.02 is fine for most things.

    Returns:
        a confirmation string
    """
    logger.info(f"Typing {len(text)} characters...")

    # pyautogui.write() handles most printable chars but chokes on some unicode
    # and special characters. we handle common cases explicitly.
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            pyautogui.write(line, interval=interval)
        if i < len(lines) - 1:
            pyautogui.press("enter")

    preview = text[:50].replace("\n", "\\n")
    return f"Typed: '{preview}{'...' if len(text) > 50 else ''}'"


def press_key(key: str) -> str:
    """
    Press a single key or keyboard shortcut.

    Uses pyautogui's key name format. Examples: 'enter', 'tab', 'escape',
    'ctrl+s', 'cmd+z', 'ctrl+shift+p'.

    Args:
        key: the key name or shortcut string

    Returns:
        a confirmation string
    """
    logger.info(f"Pressing key: {key}")

    if "+" in key:
        keys = [k.strip() for k in key.split("+")]
        pyautogui.hotkey(*keys)
    else:
        pyautogui.press(key)

    time.sleep(0.05)
    return f"Pressed: {key}"


def scroll_at_element(element_id: int, direction: str = "down", clicks: int = 3) -> str:
    """
    Scroll at the position of a screen element.

    Args:
        element_id: where to scroll
        direction: 'up' or 'down'
        clicks: how many scroll ticks to send

    Returns:
        a confirmation string
    """
    elem = _find_element(element_id)
    x, y = elem["x"], elem["y"]

    pyautogui.moveTo(x, y, duration=0.1)
    amount = clicks if direction == "up" else -clicks
    pyautogui.scroll(amount, x=x, y=y)

    return f"Scrolled {direction} {clicks} ticks at element {element_id} ({x}, {y})"


def get_screen_elements() -> list[dict]:
    """
    Return the current list of detected screen elements.

    Useful for the model to check what's available before deciding what to click.
    Returns the contents of the latest elements JSON.
    """
    return _get_latest_elements()
