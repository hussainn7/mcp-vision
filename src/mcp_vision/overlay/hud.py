"""Transparent confirmation HUD. Headless fallback aborts restricted actions.

Space/Return confirm, Esc abort. Timeout expires -> abort (auto-pause).
"""

from __future__ import annotations

import os
import sys
import subprocess
import math
from typing import Callable

from mcp_vision.core.models import BoundingBox
from mcp_vision.log import get_logger

log = get_logger("mcp_vision.hud")

ConfirmFn = Callable[[str, BoundingBox | None, float], bool]

_forced: bool | None = None
_impl: ConfirmFn | None = None


def set_forced_result(value: bool | None) -> None:
    """Tests: skip the window and return this value."""
    global _forced
    _forced = value


def set_impl(fn: ConfirmFn | None) -> None:
    global _impl
    _impl = fn


def _has_display() -> bool:
    if os.environ.get("MCP_VISION_HUD") == "off":
        return False
    if sys.platform == "darwin":
        return os.environ.get("MCP_VISION_HUD", "on") != "off"
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _tty_confirm(prompt: str, timeout_s: float) -> bool:
    if not sys.stdin.isatty():
        log.warning("no tty for HUD fallback; aborting")
        return False
    print(f"{prompt}  [Space/Enter confirm, Esc abort] timeout={timeout_s:.0f}s", file=sys.stderr)
    try:
        line = input("confirm [y/N]: ").strip().lower()
    except EOFError:
        return False
    return line in {"y", "yes", " "}


_MAC_DIALOG = '''on run argv
    activate
    set answer to display dialog (item 1 of argv) with title "MCP-Vision needs permission" buttons {"Deny", "Allow once"} default button "Deny" cancel button "Deny" with icon caution giving up after (item 2 of argv as integer)
    if gave up of answer then return "denied"
    if button returned of answer is "Allow once" then return "allowed"
    return "denied"
end run'''


def _mac_notify(prompt: str) -> None:
    """Bounce the user even when the host is buried under other windows."""
    text = prompt.replace("\\", "\\\\").replace('"', '\\"')[:180]
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{text}" with title "MCP-Vision needs permission" '
             f'sound name "Sosumi"'],
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _mac_confirm(prompt: str, timeout_s: float) -> bool:
    """A separate native dialog works even when MCP stdin is a pipe."""
    timeout = max(1, min(120, math.ceil(timeout_s)))
    _mac_notify(prompt)
    try:
        result = subprocess.run(
            ["osascript", "-e", _MAC_DIALOG, prompt, str(timeout)],
            capture_output=True, text=True, timeout=timeout + 3,
        )
        return result.returncode == 0 and result.stdout.strip() == "allowed"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _qt_confirm(prompt: str, bbox: BoundingBox | None, timeout_s: float) -> bool:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QColor, QPainter, QPen
    from PySide6.QtWidgets import QApplication, QLabel, QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    result = {"ok": False}

    class Overlay(QWidget):
        def __init__(self) -> None:
            super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setWindowState(Qt.WindowState.WindowFullScreen)
            self._bbox = bbox

        def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(0, 0, 0, 40))
            if self._bbox:
                p.setPen(QPen(QColor(255, 80, 80), 3))
                p.drawRect(self._bbox.x, self._bbox.y, self._bbox.w, self._bbox.h)
            p.end()

        def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                result["ok"] = True
                self.close()
            elif event.key() == Qt.Key.Key_Escape:
                result["ok"] = False
                self.close()

    overlay = Overlay()
    label = QLabel("Press Space to Confirm / Esc to Abort\n" + prompt, overlay)
    label.setStyleSheet("color: white; font-size: 18px; background: rgba(0,0,0,160); padding: 12px;")
    label.adjustSize()
    label.move(24, 24)
    overlay.show()
    overlay.raise_()
    overlay.activateWindow()

    def timeout() -> None:
        log.info("HUD timeout — auto-pause")
        result["ok"] = False
        overlay.close()

    QTimer.singleShot(int(timeout_s * 1000), timeout)
    app.exec()
    return bool(result["ok"])


def confirm_action(
    prompt: str,
    bbox: BoundingBox | None = None,
    timeout_s: float = 20.0,
) -> bool:
    if _forced is not None:
        return _forced
    if _impl is not None:
        return _impl(prompt, bbox, timeout_s)
    if _has_display():
        if sys.platform == "darwin":
            return _mac_confirm(prompt, timeout_s)
        try:
            import PySide6  # noqa: F401
            return _qt_confirm(prompt, bbox, timeout_s)
        except Exception as e:
            log.info("HUD unavailable (%s); tty fallback", e)
    return _tty_confirm(prompt, timeout_s)
