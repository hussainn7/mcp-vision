"""Mouse/keyboard backends. The real one uses pyautogui; tests inject a fake."""

from __future__ import annotations

from typing import Protocol

from mcp_vision.log import get_logger

log = get_logger("mcp_vision.actuate")


class Actuator(Protocol):
    def click(self, x: int, y: int, click_type: str = "single") -> None: ...
    def type_text(self, text: str, press_enter: bool = False) -> None: ...
    def press(self, keys: list[str]) -> None: ...


class RecordingActuator:
    """In-memory actuator for tests. Never touches the OS."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def click(self, x: int, y: int, click_type: str = "single") -> None:
        self.calls.append(("click", (x, y, click_type)))

    def type_text(self, text: str, press_enter: bool = False) -> None:
        self.calls.append(("type", (text, press_enter)))

    def press(self, keys: list[str]) -> None:
        self.calls.append(("press", (tuple(keys),)))


class PyAutoGUIActuator:
    def click(self, x: int, y: int, click_type: str = "single") -> None:
        import pyautogui
        pyautogui.moveTo(x, y, duration=0.1)
        kind = (click_type or "single").lower()
        if kind in ("double", "double_click"):
            pyautogui.doubleClick(x, y)
        elif kind in ("right", "right_click"):
            pyautogui.rightClick(x, y)
        else:
            pyautogui.click(x, y)

    def type_text(self, text: str, press_enter: bool = False) -> None:
        import pyautogui
        pyautogui.write(text, interval=0.02)
        if press_enter:
            pyautogui.press("enter")

    def press(self, keys: list[str]) -> None:
        import pyautogui
        cleaned = [k.strip().lower() for k in keys if k.strip()]
        if not cleaned:
            return
        if len(cleaned) == 1:
            pyautogui.press(cleaned[0])
        else:
            pyautogui.hotkey(*cleaned)


_default: Actuator | None = None


def get_actuator() -> Actuator:
    global _default
    if _default is None:
        _default = PyAutoGUIActuator()
    return _default


def set_actuator(actuator: Actuator | None) -> None:
    global _default
    _default = actuator
