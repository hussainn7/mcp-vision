"""FastMCP stdio server. Logs go to stderr; stdout is JSON-RPC only."""

from __future__ import annotations

from typing import Any

from mcp_vision.core.actuate import Actuator, get_actuator, set_actuator
from mcp_vision.core.capture import Frame, Grabber, capture_display
from mcp_vision.core.models import ActionResult, Policy, ScreenInspectionResult
from mcp_vision.core.parser import inspect_image
from mcp_vision.log import configure, get_logger

configure()
log = get_logger("mcp_vision.server")

_last: ScreenInspectionResult | None = None
_last_frame: Frame | None = None
_grabber: Grabber | None = None


def reset_session() -> None:
    global _last, _last_frame, _grabber
    _last = None
    _last_frame = None
    _grabber = None
    set_actuator(None)


def set_grabber(grabber: Grabber | None) -> None:
    global _grabber
    _grabber = grabber


def last_inspection() -> ScreenInspectionResult | None:
    return _last


def _screen_xy(element_id: int) -> tuple[int, int, int]:
    if _last is None or _last_frame is None:
        raise RuntimeError("call inspect_screen first")
    el = _last.element(element_id)
    if el is None:
        raise KeyError(f"element {element_id} not in last inspection")
    frame = _last_frame
    x = int(frame.monitor.get("left", 0) + el.cx * frame.scale)
    y = int(frame.monitor.get("top", 0) + el.cy * frame.scale)
    return x, y, element_id


def inspect_screen(display_id: int = 0) -> ScreenInspectionResult:
    """Capture a display and return numbered UI elements (SoM)."""
    global _last, _last_frame
    frame = capture_display(display_id, grabber=_grabber)
    result = inspect_image(
        frame.image, display_id=display_id, scale=frame.scale, png=frame.png, ocr=True,
    )
    _last, _last_frame = result, frame
    log.info("inspect display=%s elements=%s %sx%s", display_id, len(result.elements),
             result.width, result.height)
    return result


def click_element(element_id: int, click_type: str = "single") -> ActionResult:
    """Click a numbered element from the last inspect_screen call."""
    try:
        x, y, eid = _screen_xy(element_id)
    except (RuntimeError, KeyError) as e:
        return ActionResult(ok=False, message=str(e), element_id=element_id, policy=Policy.ROUTINE_WRITE)
    get_actuator().click(x, y, click_type)
    return ActionResult(
        ok=True, message=f"clicked {element_id} ({click_type}) at ({x},{y})",
        element_id=eid, policy=Policy.ROUTINE_WRITE,
    )


def type_text(element_id: int, text: str, press_enter: bool = False) -> ActionResult:
    """Focus an element by id, then type. Set press_enter to submit."""
    try:
        x, y, eid = _screen_xy(element_id)
    except (RuntimeError, KeyError) as e:
        return ActionResult(ok=False, message=str(e), element_id=element_id, policy=Policy.ROUTINE_WRITE)
    act = get_actuator()
    act.click(x, y, "single")
    act.type_text(text, press_enter=press_enter)
    return ActionResult(
        ok=True, message=f"typed {len(text)} chars into {element_id}",
        element_id=eid, policy=Policy.ROUTINE_WRITE,
    )


def press_key_combination(keys: list[str]) -> ActionResult:
    """Press a key or chord, e.g. ['cmd', 's'] or ['enter']."""
    if not keys:
        return ActionResult(ok=False, message="keys must be a non-empty list", policy=Policy.ROUTINE_WRITE)
    get_actuator().press(list(keys))
    return ActionResult(ok=True, message=f"pressed {'+'.join(keys)}", policy=Policy.ROUTINE_WRITE)


def _mcp() -> Any:
    try:
        from fastmcp import FastMCP
    except ImportError:
        from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("mcp-vision")
    mcp.tool()(inspect_screen)
    mcp.tool()(click_element)
    mcp.tool()(type_text)
    mcp.tool()(press_key_combination)
    return mcp


def main() -> None:
    configure()
    log.info("starting mcp-vision on stdio")
    _mcp().run(transport="stdio")


if __name__ == "__main__":
    main()
