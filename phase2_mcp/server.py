"""
The MCP server.

This is what exposes the click/type tools over the Model Context Protocol.
It uses FastMCP from the official Python SDK, which handles all the protocol
boilerplate so you just write normal Python functions and decorate them.

Run this directly to start the server:
    python phase2_mcp/server.py

By default it runs on stdio transport, which is how the orchestrator talks
to it in Phase 3. If you want to test it standalone, you can use the MCP
CLI inspector:
    npx @modelcontextprotocol/inspector python phase2_mcp/server.py
"""

import sys
from pathlib import Path

from loguru import logger
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).parent.parent))
from phase2_mcp.tools import (
    click_element,
    get_screen_elements,
    press_key,
    right_click_element,
    scroll_at_element,
    type_code,
)

# initialize the FastMCP server with a descriptive name
mcp = FastMCP("screen-agent")


@mcp.tool()
def click(element_id: int) -> str:
    """
    Click a UI element on the screen.

    Looks up the element's center coordinates from the last OmniParser scan
    and sends a mouse click there. Use get_elements first to see what's available.

    Args:
        element_id: the number shown in the annotated screenshot (1-indexed)
    """
    return click_element(element_id, double=False)


@mcp.tool()
def double_click(element_id: int) -> str:
    """
    Double-click a UI element on the screen.

    Same as click() but sends two clicks in quick succession. Useful for
    opening files in editors or selecting words in text fields.

    Args:
        element_id: the number shown in the annotated screenshot (1-indexed)
    """
    return click_element(element_id, double=True)


@mcp.tool()
def right_click(element_id: int) -> str:
    """
    Right-click a UI element to open its context menu.

    Args:
        element_id: the number shown in the annotated screenshot (1-indexed)
    """
    return right_click_element(element_id)


@mcp.tool()
def type_text(text: str) -> str:
    """
    Type text at the current cursor position.

    The text is typed character by character using pyautogui. Newlines in
    the string are converted to Enter keypresses. Make sure the right input
    field is focused before calling this.

    Args:
        text: the string to type (supports newlines)
    """
    return type_code(text)


@mcp.tool()
def press(key: str) -> str:
    """
    Press a keyboard key or shortcut.

    Examples:
        press("enter")
        press("tab")
        press("escape")
        press("cmd+s")         # save
        press("ctrl+shift+p")  # VS Code command palette

    Args:
        key: pyautogui key name, or a '+'-joined shortcut like 'cmd+z'
    """
    return press_key(key)


@mcp.tool()
def scroll(element_id: int, direction: str = "down", clicks: int = 3) -> str:
    """
    Scroll at the position of a screen element.

    Args:
        element_id: where to scroll (the cursor moves there first)
        direction: 'up' or 'down'
        clicks: number of scroll ticks (3 is a normal scroll, 10 is a lot)
    """
    return scroll_at_element(element_id, direction, clicks)


@mcp.tool()
def get_elements() -> list[dict]:
    """
    Return the list of UI elements detected in the last screen scan.

    Each element has an id, label (text description), x/y coordinates for
    its center, and its bounding box. Call this before deciding what to click
    so you know what's on screen.
    """
    return get_screen_elements()


if __name__ == "__main__":
    logger.info("Starting screen-agent MCP server on stdio transport...")
    mcp.run(transport="stdio")
