"""Push-to-talk flow, independent of AppKit so it can be tested anywhere.

    idle --press--> listening --release--> finalizing --transcript--> responding --done--> idle
      ^                 |cancel                 |empty/timeout/error                     |
      +-----------------+-----------------------+----------------------------------------+

Pressing the chord at any time interrupts whatever the buddy is saying or
pointing at (barge-in), like Clicky. All methods run on the main thread.

``presenter`` mirrors the flow in the notch island (listening, live
transcript, thinking, errors); the companion's observer covers the answer.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Callable
from typing import Any


def _say(text: str) -> None:
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


class _NullPresenter:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class BuddyController:
    FINAL_TIMEOUT = 3.5          # seconds to wait for a transcript after release
    # seconds held alone before it counts (other apps' ⌃⌥ shortcuts)
    PRESS_DELAY = 0.15

    def __init__(self, *, companion: Any, overlay: Any, loop: asyncio.AbstractEventLoop,
                 call_later: Callable[[float, Callable[[], None]], Any],
                 on_main: Callable[..., Any], listener: Any = None, setup_error: str = "",
                 status: Callable[[str], None] | None = None, say: Callable[[str], None] = _say,
                 presenter: Any = None, on_result: Callable[[str, Any], None] | None = None,
                 on_setup_needed: Callable[[str], None] | None = None, press_delay: float = 0.0,
                 sound: Callable[[str], None] | None = None):
        self.companion = companion
        self.overlay = overlay
        self.loop = loop
        self.call_later = call_later
        self.on_main = on_main
        self.listener = listener
        self.setup_error = setup_error
        self.status = status or (lambda text: None)
        self.say = say
        self.presenter = presenter or _NullPresenter()
        self.on_result = on_result or (lambda transcript, result: None)
        self.on_setup_needed = on_setup_needed or (lambda message: None)
        self.state = "idle"
        self.transcript = ""
        self.generation = 0
        self.hotkey_mode = "none"
        self.shortcut = "Control+Option"     # talk shortcut, in words and in keys
        self.shortcut_keys = "⌃⌥"
        self.last_result = None
        self.press_delay = press_delay
        self.sound = sound or (lambda name: None)
        self._pressing = 0                   # press number waiting out press_delay, 0 = none
        self._presses = 0
        self._early = False                  # mic started at the press

    # -- hotkey -------------------------------------------------------------------
    def on_press(self) -> None:
        if self.press_delay <= 0:
            self._press()
            return
        self._presses += 1
        self._pressing = press = self._presses
        self._listen_early()
        self.call_later(self.press_delay, lambda: self._press(press))

    def _listen_early(self) -> None:
        # mic on now (first word); hush Plip so it isn't recorded
        self._early = False
        if self.setup_error or self.companion is None or self.listener is None or \
                self.state in {"listening", "finalizing"}:
            return
        try:
            self.listener.start()
        except Exception:
            return
        self._early = True
        hush = getattr(self.companion, "hush", None)
        if hush is not None and self.loop is not None:
            self.loop.call_soon_threadsafe(hush)

    def _drop_early(self) -> None:
        if self._early:
            self._early = False
            self.listener.cancel()

    def _press(self, press: int = 0) -> None:
        if press:
            if press != self._pressing:
                return                       # released, or another key joined: not a request
            self._pressing = 0
        if self.setup_error or self.companion is None or self.listener is None:
            message = self.setup_error or "Speech input is unavailable."
            self.status("Needs setup: " + message)
            self.presenter.failed(message, True)
            self.say("I need a little setup first. I opened my settings for you.")
            self.on_setup_needed(message)
            return
        self.generation += 1
        self.loop.call_soon_threadsafe(self.companion.interrupt, self.generation)
        warm = getattr(self.companion, "warm_brain", None)
        if warm is not None:                         # brain warms while they talk (~0.3 s)
            self.loop.call_soon_threadsafe(warm)
        self.state = "listening"
        self.transcript = ""
        self.overlay.set_state("listening")
        self.presenter.listening()
        self.status("Listening...")
        if self._early:                              # mic already on
            self._early = False
            return
        try:
            self.listener.start()
        except Exception as exc:
            self._idle(f"Microphone problem: {exc}")

    def ask(self, text: str) -> None:
        """A button's request (Yes, Keep going), handled as if spoken."""
        if self.setup_error or self.companion is None:
            self.presenter.failed(self.setup_error or "I'm still waking up. Try again in a second.", True)
            return
        if self.state in {"listening", "finalizing"} and self.listener is not None:
            self.listener.cancel()
        self.generation += 1
        self.loop.call_soon_threadsafe(self.companion.interrupt, self.generation)
        self.overlay.set_state("thinking")
        self.presenter.asked(text)
        self.state = "finalizing"
        self.on_final(text)

    def on_release(self) -> None:
        if self._pressing:                   # a tap: only stops Plip
            self.stop()
            return
        if self.state != "listening":
            return
        self.state = "finalizing"
        self.overlay.set_state("thinking")
        self.presenter.thinking()
        self.status("Thinking...")
        self.companion.prefetch()
        self.listener.release()
        generation = self.generation
        tail = getattr(self.listener, "tail", 0) or 0       # parakeet keeps the mic on a beat longer
        if tail > 0:
            self.call_later(tail + 0.02, lambda: self._toss(generation))
        else:
            self.sound("sent")                           # mic is off now, so it can't hear it
        self.call_later(self.FINAL_TIMEOUT, lambda: self._final_timeout(generation))

    def _toss(self, generation: int) -> None:
        if generation == self.generation and self.state != "listening":
            self.sound("sent")

    def stop(self) -> None:
        """Stop (island, menu bar, quick tap): whatever Plip is doing, including a pending answer."""
        self._pressing = 0
        if self.state in {"listening", "finalizing"} and self.listener is not None:
            self.listener.cancel()
        self._drop_early()
        self.generation += 1
        if self.companion is not None and self.loop is not None:
            self.loop.call_soon_threadsafe(self.companion.interrupt, self.generation)
        self._idle(f"Ready - hold {self.shortcut}")
        self.presenter.idle()

    def on_cancel(self) -> None:
        if self._pressing:                   # another app's shortcut: leave Plip alone
            self._pressing = 0
            self._drop_early()
            return
        if self.state in {"listening", "finalizing"}:
            self.listener.cancel()
            self._idle(f"Ready - hold {self.shortcut}")
            self.presenter.idle()

    # -- speech -------------------------------------------------------------------
    def on_level(self, level: float) -> None:
        if self.state == "listening":
            if hasattr(self.overlay, "set_level"):
                self.overlay.set_level(level)
            self.presenter.level(level)

    def on_partial(self, text: str) -> None:
        if self.state in {"listening", "finalizing"} and text:
            self.transcript = text
            self.status("Heard: " + text[-70:])
            self.presenter.transcript(text)

    def on_final(self, text: str) -> None:
        if self.state != "finalizing":
            return
        text = " ".join((text or "").split())
        if not text:
            self._idle(f"Didn't catch that - hold {self.shortcut} and talk")
            self.presenter.failed(f"I didn't catch that. Hold {self.shortcut_keys} and try again.")
            return
        self.state = "responding"
        self.transcript = text
        self.presenter.transcript(text)
        self.status(f"You: {text[:70]}")
        generation = self.generation
        # The token makes the companion drop this turn if a newer press already
        # interrupted, even when the interrupt ran before the turn started.
        future = asyncio.run_coroutine_threadsafe(self.companion.respond(text, token=generation), self.loop)
        future.add_done_callback(lambda done: self.on_main(self._finished, done, generation))

    def on_error(self, message: str) -> None:
        if self.state in {"listening", "finalizing"}:
            # Release will no longer reach the listener once we are idle, so
            # stop the microphone here or it stays live until the next press.
            self.listener.cancel()
            self._idle(message)
            self.presenter.failed(message)
            self.say("Sorry, I couldn't hear you.")

    # -- internals ----------------------------------------------------------------
    def _final_timeout(self, generation: int) -> None:
        if generation == self.generation and self.state == "finalizing":
            self.listener.cancel()
            self._idle(f"Didn't catch that - hold {self.shortcut} and talk")
            self.presenter.failed(f"I didn't catch that. Hold {self.shortcut_keys} and try again.")

    def _finished(self, future, generation: int) -> None:
        if generation != self.generation:
            return
        self.state = "idle"
        try:
            result = future.result()
        except Exception as exc:
            self.status(f"Error: {exc}")
            self.presenter.failed("Something went wrong on my end. Try asking again.")
            return
        self.last_result = result
        self.on_result(self.transcript, result)
        if result.state == "done":
            first = result.timings.get("first_speech")
            self.status("Ready" + (f" - answered in {first / 1000:.1f}s" if first else ""))
        elif result.state == "error":
            self.status(result.error or "Something went wrong")

    def _idle(self, status: str) -> None:
        self.state = "idle"
        self.overlay.set_state("idle")
        self.status(status)
