"""
Tiny always-on-top overlay for agent status + final answers.

On macOS we use a native NSPanel at a high window level — tkinter -topmost
is not enough and gets buried by Chrome/fullscreen apps.
"""

import queue
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Any
from datetime import datetime
from loguru import logger


class StepStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    VERIFICATION_FAILED = "verification_failed"


@dataclass
class PlanStep:
    description: str
    expected_element: Optional[str] = None
    expected_role: Optional[str] = None
    action: Optional[str] = None
    status: StepStatus = StepStatus.PENDING
    result: Optional[str] = None


@dataclass
class ActionRecord:
    timestamp: str
    action: str
    result: str
    success: bool


def _truncate(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


class Overlay:
    """Small always-on-top status pill with an answer area."""

    def __init__(self, width: int = 280, height: int = 110):
        self.width = width
        self.height = height
        self._queue: queue.Queue = queue.Queue()
        self._root: Optional[tk.Tk] = None
        self._running = False
        self._user_response: Optional[bool] = None
        self._user_event = threading.Event()
        self._plan_steps: list[PlanStep] = []
        self._action_history: list[ActionRecord] = []
        self._task: str = ""
        self._current_step_idx: int = 0
        self._done = False
        self._prompt_msg: Optional[str] = None
        self._agent_thread: Optional[threading.Thread] = None
        self._agent_fn: Optional[Callable] = None
        self._geometry_cache: Optional[tuple[int, int, int, int]] = None
        self._answer: str = ""
        self._status: str = "ready"

        # Native macOS panel (force-float)
        self._ns_panel: Any = None
        self._ns_task: Any = None
        self._ns_status: Any = None
        self._ns_answer: Any = None
        self._use_native = sys.platform == "darwin"

        # Tk fallback widgets
        self._task_label = None
        self._status_label = None
        self._answer_label = None

    def run(self, task: str = "", plan_steps: Optional[list[PlanStep]] = None, agent_fn: Optional[Callable] = None):
        self._task = task
        self._plan_steps = plan_steps or []
        self._action_history = []
        self._current_step_idx = 0
        self._done = False
        self._prompt_msg = None
        self._answer = ""
        self._status = "starting..."
        self._agent_fn = agent_fn
        self._running = True

        if agent_fn:
            self._agent_thread = threading.Thread(target=self._run_agent, daemon=True)
            self._agent_thread.start()

        self._run_tk()

    def _run_agent(self):
        try:
            if self._agent_fn:
                self._agent_fn(self)
        except Exception as e:
            self._queue.put({"type": "status", "message": f"error: {e}"})

    def _run_tk(self):
        self._root = tk.Tk()
        self._root.title("agent-overlay-host")
        # Tiny hidden host window — only used to pump timers on the main thread
        self._root.geometry("1x1+0+0")
        self._root.withdraw()

        if self._use_native:
            ok = self._create_native_panel()
            if not ok:
                self._use_native = False
                self._root.deiconify()
                self._build_tk_ui()
        else:
            self._build_tk_ui()

        self._root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._force_float()
        self._poll_queue()
        self._root.mainloop()

    def _on_close(self):
        self._running = False
        try:
            if self._ns_panel is not None:
                self._ns_panel.orderOut_(None)
                self._ns_panel = None
        except Exception:
            pass
        if self._root:
            self._root.destroy()

    def _create_native_panel(self) -> bool:
        """Create an NSPanel that stays above normal apps / Spaces."""
        try:
            from AppKit import (
                NSApplication,
                NSColor,
                NSFont,
                NSMakeRect,
                NSPanel,
                NSPopUpMenuWindowLevel,
                NSTextField,
                NSView,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorStationary,
                NSWindowStyleMaskBorderless,
                NSWindowStyleMaskNonactivatingPanel,
                NSBackingStoreBuffered,
                NSViewWidthSizable,
                NSViewHeightSizable,
            )
            from Foundation import NSMakePoint

            # Ensure NSApp exists (tk already created one, but be safe)
            NSApplication.sharedApplication()

            screen = None
            try:
                from AppKit import NSScreen
                screen = NSScreen.mainScreen()
                frame = screen.frame()
                x = frame.size.width - self.width - 12
                # Cocoa origin is bottom-left
                y = frame.size.height - self.height - 36
            except Exception:
                x, y = 40, 40

            rect = NSMakeRect(x, y, self.width, self.height)
            style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                rect, style, NSBackingStoreBuffered, False
            )
            panel.setTitle_("agent")
            panel.setOpaque_(False)
            panel.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(0.07, 0.94))
            panel.setHasShadow_(True)
            panel.setFloatingPanel_(True)
            panel.setBecomesKeyOnlyIfNeeded_(True)
            panel.setHidesOnDeactivate_(False)
            panel.setWorksWhenModal_(True)
            panel.setLevel_(NSPopUpMenuWindowLevel)  # 101 — above normal apps
            panel.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            panel.setMovableByWindowBackground_(True)
            panel.setIgnoresMouseEvents_(False)

            content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.width, self.height))
            content.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

            def _label(text, font_size, bold, y, height, color):
                field = NSTextField.alloc().initWithFrame_(NSMakeRect(10, y, self.width - 20, height))
                field.setStringValue_(text)
                field.setBezeled_(False)
                field.setDrawsBackground_(False)
                field.setEditable_(False)
                field.setSelectable_(False)
                if bold:
                    field.setFont_(NSFont.boldSystemFontOfSize_(font_size))
                else:
                    field.setFont_(NSFont.systemFontOfSize_(font_size))
                field.setTextColor_(color)
                field.setAutoresizingMask_(NSViewWidthSizable)
                content.addSubview_(field)
                return field

            white = NSColor.colorWithCalibratedWhite_alpha_(0.95, 1.0)
            gray = NSColor.colorWithCalibratedWhite_alpha_(0.55, 1.0)
            blue = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.39, 0.82, 1.0, 1.0)

            # Cocoa y grows upward
            self._ns_task = _label(_truncate(self._task or "idle", 42), 11, True, self.height - 28, 18, white)
            self._ns_status = _label(self._status, 10, False, self.height - 46, 16, gray)
            self._ns_answer = _label("", 11, False, 10, self.height - 58, blue)

            panel.setContentView_(content)
            panel.orderFrontRegardless()
            self._ns_panel = panel
            self._geometry_cache = (int(x), 12, self.width, self.height)
            logger.info("Native NSPanel overlay pinned at PopUpMenu level")
            return True
        except Exception as e:
            logger.warning(f"Native overlay failed, falling back to tk: {e}")
            return False

    def _force_float(self):
        """Re-assert high window level so nothing can bury the overlay."""
        if self._ns_panel is not None:
            try:
                from AppKit import NSPopUpMenuWindowLevel
                self._ns_panel.setLevel_(NSPopUpMenuWindowLevel)
                self._ns_panel.setHidesOnDeactivate_(False)
                self._ns_panel.orderFrontRegardless()
            except Exception as e:
                logger.debug(f"force_float panel: {e}")
            return

        if not self._root:
            return
        try:
            self._root.lift()
            self._root.attributes("-topmost", True)
        except Exception:
            pass
        try:
            from AppKit import (
                NSApp,
                NSPopUpMenuWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorStationary,
            )
            behavior = (
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            for window in NSApp.windows():
                try:
                    title = str(window.title() or "")
                    if title in {"agent", "agent-overlay-host"} or window.isVisible():
                        window.setLevel_(NSPopUpMenuWindowLevel)
                        window.setCollectionBehavior_(behavior)
                        window.setHidesOnDeactivate_(False)
                        window.orderFrontRegardless()
                except Exception:
                    continue
        except Exception:
            pass

    def _build_tk_ui(self):
        root = self._root
        root.deiconify()
        root.geometry(f"{self.width}x{self.height}")
        root.configure(bg="#111111")
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.94)
        root.resizable(False, False)
        sw = root.winfo_screenwidth()
        root.geometry(f"+{sw - self.width - 12}+12")

        pad = {"padx": 10, "pady": 2}
        self._task_label = tk.Label(
            root, text=_truncate(self._task or "idle", 42),
            font=("SF Pro Text", 10, "bold"), fg="#f2f2f2", bg="#111111", anchor="w",
        )
        self._task_label.pack(fill="x", **pad)
        self._status_label = tk.Label(
            root, text=self._status, font=("SF Pro Text", 9),
            fg="#8e8e93", bg="#111111", anchor="w",
        )
        self._status_label.pack(fill="x", padx=10)
        self._answer_label = tk.Label(
            root, text="", font=("SF Pro Text", 10),
            fg="#64d2ff", bg="#111111", anchor="nw", justify="left",
            wraplength=self.width - 20,
        )
        self._answer_label.pack(fill="both", expand=True, padx=10, pady=(4, 8))

    def get_geometry(self) -> Optional[tuple[int, int, int, int]]:
        if self._ns_panel is not None:
            try:
                frame = self._ns_panel.frame()
                screen_h = self._ns_panel.screen().frame().size.height
                # convert cocoa bottom-left to top-left screen coords for masking
                x = int(frame.origin.x)
                y = int(screen_h - frame.origin.y - frame.size.height)
                w = int(frame.size.width)
                h = int(frame.size.height)
                self._geometry_cache = (x, y, w, h)
            except Exception:
                pass
        return self._geometry_cache

    def _set_native_text(self, field, text: str, color=None):
        if field is None:
            return
        try:
            field.setStringValue_(text or "")
            if color is not None:
                field.setTextColor_(color)
        except Exception:
            pass

    def _poll_queue(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass

        if self._running and self._root:
            # Force float every tick — this is the important part
            self._force_float()
            self.get_geometry()
            self._root.after(100, self._poll_queue)

    def _handle_msg(self, msg: dict):
        typ = msg.get("type")

        if typ == "task":
            self._task = msg.get("task", "")
            text = _truncate(self._task or "idle", 42)
            if self._use_native:
                self._set_native_text(self._ns_task, text)
            elif self._task_label:
                self._task_label.config(text=text)

        elif typ == "plan":
            self._plan_steps = msg.get("steps", [])
            self._current_step_idx = msg.get("current", 0)

        elif typ == "step_status":
            idx = msg.get("index", self._current_step_idx)
            status = msg.get("status")
            result = msg.get("result")
            if 0 <= idx < len(self._plan_steps):
                self._plan_steps[idx].status = status
                if result:
                    self._plan_steps[idx].result = result
            self._current_step_idx = msg.get("current", self._current_step_idx)

        elif typ == "action":
            self._action_history.append(
                ActionRecord(
                    timestamp=msg.get("timestamp", datetime.now().strftime("%H:%M:%S")),
                    action=msg.get("action", ""),
                    result=msg.get("result", ""),
                    success=msg.get("success", True),
                )
            )
            action = _truncate(msg.get("action", ""), 46)
            if self._use_native:
                self._set_native_text(self._ns_status, action)
            elif self._status_label:
                self._status_label.config(text=action, fg="#8e8e93")

        elif typ == "prompt":
            self._prompt_msg = msg.get("message", "")
            self._user_response = None
            self._user_event.clear()
            if self._use_native:
                from AppKit import NSColor
                orange = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.62, 0.04, 1.0)
                self._set_native_text(self._ns_answer, self._prompt_msg, orange)
                self._set_native_text(self._ns_status, "needs you", orange)
            elif self._answer_label:
                self._answer_label.config(text=self._prompt_msg, fg="#ff9f0a")
                self._status_label.config(text="needs you", fg="#ff9f0a")

        elif typ == "status":
            self._status = msg.get("message", "")
            text = _truncate(self._status, 46)
            if self._use_native:
                self._set_native_text(self._ns_status, text)
            elif self._status_label:
                self._status_label.config(text=text, fg="#8e8e93")

        elif typ == "answer":
            self._answer = msg.get("message", "")
            if self._use_native:
                from AppKit import NSColor
                blue = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.39, 0.82, 1.0, 1.0)
                self._set_native_text(self._ns_answer, self._answer, blue)
                self._resize_native_for_answer()
            elif self._answer_label:
                self._answer_label.config(text=self._answer, fg="#64d2ff")

        elif typ == "done":
            self._done = True
            message = msg.get("message", "done")
            if self._use_native:
                from AppKit import NSColor
                green = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.20, 0.78, 0.35, 1.0)
                self._set_native_text(self._ns_status, "done", green)
                if message:
                    self._answer = message
                    self._set_native_text(self._ns_answer, message, green)
                    self._resize_native_for_answer()
            else:
                self._status_label.config(text="done", fg="#34c759")
                if message and self._answer_label:
                    self._answer = message
                    self._answer_label.config(text=message, fg="#34c759")

    def _resize_native_for_answer(self):
        if self._ns_panel is None or not self._answer:
            return
        try:
            lines = max(2, min(10, (len(self._answer) // 34) + 2))
            new_h = 70 + lines * 16
            frame = self._ns_panel.frame()
            # keep top edge fixed while growing down in cocoa coords
            top = frame.origin.y + frame.size.height
            from Foundation import NSMakeRect
            self._ns_panel.setFrame_display_(
                NSMakeRect(frame.origin.x, top - new_h, self.width, new_h), True
            )
            if self._ns_answer is not None:
                self._ns_answer.setFrame_(NSMakeRect(10, 10, self.width - 20, new_h - 58))
            if self._ns_task is not None:
                self._ns_task.setFrame_(NSMakeRect(10, new_h - 28, self.width - 20, 18))
            if self._ns_status is not None:
                self._ns_status.setFrame_(NSMakeRect(10, new_h - 46, self.width - 20, 16))
        except Exception:
            pass

    # Public API --------------------------------------------------------------

    def update_task(self, task: str):
        self._queue.put({"type": "task", "task": task})

    def update_plan(self, steps: list[PlanStep], current: int = 0):
        self._queue.put({"type": "plan", "steps": steps, "current": current})

    def update_step(self, index: int, status: StepStatus, result: Optional[str] = None, current: Optional[int] = None):
        self._queue.put(
            {"type": "step_status", "index": index, "status": status, "result": result, "current": current}
        )

    def add_action(self, action: str, result: str, success: bool = True):
        ts = datetime.now().strftime("%H:%M:%S")
        self._queue.put({"type": "action", "timestamp": ts, "action": action, "result": result, "success": success})

    def set_status(self, message: str):
        self._queue.put({"type": "status", "message": message})

    def set_answer(self, message: str):
        self._queue.put({"type": "answer", "message": message})

    def prompt_user(self, message: str, timeout: Optional[float] = None) -> bool:
        self._queue.put({"type": "prompt", "message": message})
        self._user_event.wait(timeout=timeout or 0.1)
        return True

    def mark_done(self, message: str = "Task complete"):
        self._queue.put({"type": "done", "message": message})

    def stop(self):
        self._running = False
        if self._root:
            self._root.after(0, self._on_close)


_overlay_instance: Optional[Overlay] = None


def get_overlay() -> Overlay:
    global _overlay_instance
    if _overlay_instance is None:
        _overlay_instance = Overlay()
    return _overlay_instance


def run_overlay(task: str = "", plan_steps: Optional[list] = None, agent_fn: Optional[Callable] = None):
    get_overlay().run(task, plan_steps, agent_fn)


def stop_overlay():
    global _overlay_instance
    if _overlay_instance:
        _overlay_instance.stop()
        _overlay_instance = None
