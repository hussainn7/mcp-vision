"""
Lightweight tkinter overlay for agent status display.

Always-on-top window showing:
- Current task + plan steps with status
- Action history with timestamps
- User prompts when agent needs help

IMPORTANT: On macOS, Tkinter MUST run on the main thread.
Use overlay.run(agent_fn) which runs mainloop on main thread
and executes agent_fn in a background thread.
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable
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


class Overlay:
    """
    Always-on-top overlay window for agent status.

    Usage:
        overlay = Overlay()
        overlay.run(task="Open Safari", plan_steps=[...], agent_fn=run_agent)

    This runs the Tkinter mainloop on the main thread and agent_fn in a background thread.
    """

    def __init__(self, width: int = 420, height: int = 560):
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

    def run(self, task: str = "", plan_steps: Optional[list[PlanStep]] = None, agent_fn: Optional[Callable] = None):
        """
        Start overlay and run agent function in background.
        This MUST be called from the main thread (blocks until done).
        """
        self._task = task
        self._plan_steps = plan_steps or []
        self._action_history = []
        self._current_step_idx = 0
        self._done = False
        self._prompt_msg = None
        self._agent_fn = agent_fn

        # Run agent in background thread
        if agent_fn:
            self._agent_thread = threading.Thread(target=self._run_agent, daemon=True)
            self._agent_thread.start()

        # Run Tkinter on main thread
        self._run_tk()

    def _run_agent(self):
        """Run the agent function in background."""
        try:
            if self._agent_fn:
                self._agent_fn(self)
        except Exception as e:
            self._queue.put({"type": "status", "message": f"Agent error: {e}"})

    def _run_tk(self):
        """Main Tkinter loop (runs on main thread)."""
        self._root = tk.Tk()
        self._root.title("Agent Overlay")
        self._root.geometry(f"{self.width}x{self.height}")
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", 0.96)
        self._root.minsize(380, 300)

        # Position top-right
        self._root.update_idletasks()
        sw = self._root.winfo_screenwidth()
        self._root.geometry(f"+{sw - self.width - 16}+16")

        # Pin above all macOS Spaces using AppleScript (NSStatusWindowLevel)
        self._pin_window_above_spaces()

        self._build_ui()
        self._poll_queue()
        self._root.mainloop()

    def _pin_window_above_spaces(self):
        """Use AppleScript to set the window level so it floats above all Spaces."""
        import subprocess
        try:
            # Give the window a moment to appear before we pin it
            self._root.after(500, self._do_pin_applescript)
        except Exception as e:
            logger.warning(f"Could not schedule window pin: {e}")

    def _do_pin_applescript(self):
        """Actually execute the AppleScript to raise the window level."""
        import subprocess
        try:
            script = '''
            tell application "System Events"
                set frontmost of (first process whose name is "Python") to true
            end tell
            '''
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=3)
        except Exception as e:
            logger.debug(f"AppleScript pin skipped: {e}")
        # Schedule periodic re-lift to keep overlay above new windows
        if self._running and self._root:
            self._root.lift()
            self._root.attributes("-topmost", True)

    def _build_ui(self):
        """Build the overlay UI."""
        root = self._root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)  # plan area expands

        # Header
        header = ttk.Frame(root, padding=10)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Agent Overlay", font=("SF Pro", 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self._task_label = ttk.Label(
            header, text=self._task or "Waiting for task...", font=("SF Pro", 9), wraplength=self.width - 20
        )
        self._task_label.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        # Separator
        ttk.Separator(root, orient="horizontal").grid(row=1, column=0, sticky="ew", padx=10)

        # Plan steps
        self._plan_frame = ttk.Frame(root, padding=(10, 6))
        self._plan_frame.grid(row=2, column=0, sticky="nsew")
        self._plan_frame.columnconfigure(0, weight=1)
        self._plan_canvas = tk.Canvas(self._plan_frame, highlightthickness=0)
        self._plan_canvas.grid(row=0, column=0, sticky="nsew")
        self._plan_scroll = ttk.Scrollbar(
            self._plan_frame, orient="vertical", command=self._plan_canvas.yview
        )
        self._plan_scroll.grid(row=0, column=1, sticky="ns")
        self._plan_canvas.configure(yscrollcommand=self._plan_scroll.set)
        self._plan_inner = ttk.Frame(self._plan_canvas)
        self._plan_canvas.create_window((0, 0), window=self._plan_inner, anchor="nw")
        self._plan_inner.bind(
            "<Configure>",
            lambda e: self._plan_canvas.configure(scrollregion=self._plan_canvas.bbox("all")),
        )

        # Separator
        ttk.Separator(root, orient="horizontal").grid(row=3, column=0, sticky="ew", padx=10)

        # History
        hist_frame = ttk.LabelFrame(root, text="History", padding=8)
        hist_frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 10))
        hist_frame.columnconfigure(0, weight=1)
        hist_frame.rowconfigure(0, weight=1)

        self._hist_tree = ttk.Treeview(
            hist_frame,
            columns=("time", "action", "status"),
            show="headings",
            height=8,
        )
        self._hist_tree.heading("time", text="Time")
        self._hist_tree.heading("action", text="Action")
        self._hist_tree.heading("status", text="")
        self._hist_tree.column("time", width=60, anchor="center")
        self._hist_tree.column("action", width=220, anchor="w")
        self._hist_tree.column("status", width=40, anchor="center")
        self._hist_tree.grid(row=0, column=0, sticky="nsew")

        hist_scroll = ttk.Scrollbar(hist_frame, orient="vertical", command=self._hist_tree.yview)
        hist_scroll.grid(row=0, column=1, sticky="ns")
        self._hist_tree.configure(yscrollcommand=hist_scroll.set)

        # User prompt area (hidden by default)
        self._prompt_frame = ttk.Frame(root, padding=8)
        self._prompt_label = ttk.Label(
            self._prompt_frame, text="", wraplength=self.width - 40, foreground="#cc3300", font=("SF Pro", 9, "bold")
        )
        self._prompt_label.pack(anchor="w", pady=(0, 6))
        btn_frame = ttk.Frame(self._prompt_frame)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="Continue", command=lambda: self._respond(True)).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Abort", command=lambda: self._respond(False)).pack(side="right")

        # Status bar
        self._status_var = tk.StringVar(value="Ready")
        ttk.Label(root, textvariable=self._status_var, anchor="w", padding=(10, 4)).grid(
            row=5, column=0, sticky="ew"
        )

        self._render_plan()
        self._running = True

    def _poll_queue(self):
        """Process queue and schedule next poll."""
        try:
            while True:
                msg = self._queue.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass
        if self._running and self._root:
            # Re-assert topmost every ~2 seconds so overlay survives app switches
            self._root.lift()
            self._root.attributes("-topmost", True)
            self._root.after(50, self._poll_queue)

    def _handle_msg(self, msg: dict):
        """Apply queued update to UI."""
        typ = msg.get("type")

        if typ == "task":
            self._task = msg.get("task", "")
            if self._task_label:
                self._task_label.config(text=self._task)

        elif typ == "plan":
            self._plan_steps = msg.get("steps", [])
            self._current_step_idx = msg.get("current", 0)
            self._render_plan()

        elif typ == "step_status":
            idx = msg.get("index", self._current_step_idx)
            status = msg.get("status")
            result = msg.get("result")
            if 0 <= idx < len(self._plan_steps):
                self._plan_steps[idx].status = status
                if result:
                    self._plan_steps[idx].result = result
            self._current_step_idx = msg.get("current", self._current_step_idx)
            self._render_plan()

        elif typ == "action":
            self._action_history.append(
                ActionRecord(
                    timestamp=msg.get("timestamp", datetime.now().strftime("%H:%M:%S")),
                    action=msg.get("action", ""),
                    result=msg.get("result", ""),
                    success=msg.get("success", True),
                )
            )
            self._render_history()

        elif typ == "prompt":
            self._prompt_msg = msg.get("message", "")
            self._user_response = None
            self._user_event.clear()
            self._show_prompt(self._prompt_msg)

        elif typ == "status":
            self._status_var.set(msg.get("message", ""))

        elif typ == "done":
            self._done = True
            self._status_var.set("Done - " + msg.get("message", "Task complete"))
            self._hide_prompt()

    def _render_plan(self):
        """Rebuild plan step list."""
        for w in self._plan_inner.winfo_children():
            w.destroy()

        icons = {
            StepStatus.PENDING: "○",
            StepStatus.IN_PROGRESS: "◐",
            StepStatus.COMPLETED: "✓",
            StepStatus.FAILED: "✗",
            StepStatus.VERIFICATION_FAILED: "⚠",
        }
        colors = {
            StepStatus.PENDING: "#888",
            StepStatus.IN_PROGRESS: "#007aff",
            StepStatus.COMPLETED: "#34c759",
            StepStatus.FAILED: "#ff3b30",
            StepStatus.VERIFICATION_FAILED: "#ff9f0a",
        }

        for i, step in enumerate(self._plan_steps):
            row = ttk.Frame(self._plan_inner)
            row.pack(fill="x", pady=2)

            is_current = i == self._current_step_idx
            prefix = "▶ " if is_current else "  "
            icon = icons.get(step.status, "?")
            color = colors.get(step.status, "#888")

            label_text = f"{prefix}{icon} {step.description}"
            if step.result and step.status in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.VERIFICATION_FAILED):
                label_text += f"  → {step.result[:50]}"

            lbl = ttk.Label(row, text=label_text, font=("SF Pro", 9), foreground=color)
            lbl.pack(anchor="w")

            if step.expected_element and step.status != StepStatus.COMPLETED:
                ttk.Label(
                    row, text=f"    waiting for: {step.expected_element}", font=("SF Pro", 8), foreground="#888"
                ).pack(anchor="w")

        self._plan_inner.update_idletasks()
        self._plan_canvas.configure(scrollregion=self._plan_canvas.bbox("all"))

    def _render_history(self):
        """Update history treeview."""
        for item in self._hist_tree.get_children():
            self._hist_tree.delete(item)

        for rec in self._action_history[-20:]:
            status_icon = "✓" if rec.success else "✗"
            self._hist_tree.insert("", "end", values=(rec.timestamp, rec.action, status_icon))

        children = self._hist_tree.get_children()
        if children:
            self._hist_tree.see(children[-1])

    def _show_prompt(self, message: str):
        """Show user prompt frame."""
        self._prompt_label.config(text=message)
        self._prompt_frame.grid(row=6, column=0, sticky="ew", padx=10, pady=(0, 10))

    def _hide_prompt(self):
        """Hide user prompt frame."""
        self._prompt_frame.grid_remove()

    def _respond(self, value: bool):
        """Callback for user response buttons."""
        self._user_response = value
        self._user_event.set()
        self._hide_prompt()

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

    def prompt_user(self, message: str, timeout: Optional[float] = None) -> bool:
        """
        Show a prompt and wait for user response.
        Returns True for Continue, False for Abort.
        """
        self._queue.put({"type": "prompt", "message": message})
        self._user_event.wait(timeout=timeout)
        return self._user_response if self._user_response is not None else False

    def mark_done(self, message: str = "Task complete"):
        self._queue.put({"type": "done", "message": message})

    def stop(self):
        """Stop the overlay."""
        self._running = False
        if self._root:
            self._root.after(0, self._root.quit)

    def run_agent(self, agent_fn: Callable, *args, **kwargs):
        """
        Run agent function in background thread while overlay runs on main thread.
        This is required on macOS where tkinter must run on the main thread.
        """
        self._agent_thread = threading.Thread(
            target=self._run_agent_wrapper, args=(agent_fn, args, kwargs), daemon=True
        )
        self._agent_thread.start()
        # Start mainloop on main thread
        self._root.mainloop()
        # Wait for agent thread to finish
        self._agent_thread.join()

    def _run_agent_wrapper(self, agent_fn: Callable, args: tuple, kwargs: dict):
        """Wrap agent function to handle exceptions and stop overlay."""
        try:
            agent_fn(self, *args, **kwargs)
        except Exception as e:
            logger.error(f"Agent error: {e}")
            self._queue.put({"type": "done", "message": f"Error: {e}"})
        finally:
            if self._root:
                self._root.after(0, self._root.quit)


# Global singleton for simple usage
_overlay_instance: Optional[Overlay] = None


def get_overlay() -> Overlay:
    global _overlay_instance
    if _overlay_instance is None:
        _overlay_instance = Overlay()
    return _overlay_instance


def run_overlay(task: str = "", plan_steps: Optional[list] = None, agent_fn: Optional[Callable] = None):
    """Convenience: start global overlay and run agent."""
    get_overlay().run(task, plan_steps, agent_fn)


def stop_overlay():
    """Convenience: stop global overlay."""
    global _overlay_instance
    if _overlay_instance:
        _overlay_instance.stop()
        _overlay_instance = None