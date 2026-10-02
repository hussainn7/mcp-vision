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

    def __init__(self, *, companion: Any, overlay: Any, loop: asyncio.AbstractEventLoop,
                 call_later: Callable[[float, Callable[[], None]], Any],
                 on_main: Callable[..., Any], listener: Any = None, setup_error: str = "",
                 status: Callable[[str], None] | None = None, say: Callable[[str], None] = _say,
                 presenter: Any = None, on_result: Callable[[str, Any], None] | None = None,
                 on_setup_needed: Callable[[str], None] | None = None):
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
        self.last_result = None

    # -- hotkey -------------------------------------------------------------------
    def on_press(self) -> None:
        if self.setup_error or self.companion is None or self.listener is None:
            message = self.setup_error or "Speech input is unavailable."
            self.status("Needs setup: " + message)
            self.presenter.failed(message)
            self.say("I need a little setup first. I opened my settings for you.")
            self.on_setup_needed(message)
            return
        self.generation += 1
        self.loop.call_soon_threadsafe(self.companion.interrupt, self.generation)
        self.state = "listening"
        self.transcript = ""
        self.overlay.set_state("listening")
        self.presenter.listening()
        self.status("Listening...")
        try:
            self.listener.start()
        except Exception as exc:
            self._idle(f"Microphone problem: {exc}")

    def on_release(self) -> None:
        if self.state != "listening":
            return
        self.state = "finalizing"
        self.overlay.set_state("thinking")
        self.presenter.thinking()
        self.status("Thinking...")
        self.companion.prefetch()
        self.listener.release()
        generation = self.generation
        self.call_later(self.FINAL_TIMEOUT, lambda: self._final_timeout(generation))

    def on_cancel(self) -> None:
        if self.state in {"listening", "finalizing"}:
            self.listener.cancel()
            self._idle("Ready - hold Control+Option")
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
            self._idle("Didn't catch that - hold Control+Option and talk")
            self.presenter.failed("I didn't catch that. Hold ⌃⌥ and try again.")
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
            self._idle("Didn't catch that - hold Control+Option and talk")
            self.presenter.failed("I didn't catch that. Hold ⌃⌥ and try again.")

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
