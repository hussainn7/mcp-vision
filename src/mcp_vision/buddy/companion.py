"""The buddy's turn loop: hear -> look -> think -> talk and point.

Everything platform-specific (overlay windows, microphones, speakers, the
model provider) is injected through small ports, so this module runs and is
tested without a display or network.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Protocol

from mcp_vision.buddy.conversation import Conversation, Turn
from mcp_vision.buddy.geometry import Rect, ScreenInfo, Screenshot
from mcp_vision.buddy.pointing import PointTag, ReplyStream, SpeechChunk
from mcp_vision.buddy.prompt import SYSTEM_PROMPT, user_turn_text


# -- ports --------------------------------------------------------------------

class Brain(Protocol):
    """A streaming vision model."""

    name: str

    def stream(self, *, system: str, turns: list[Turn], detailed: bool = False) -> AsyncIterator[str]: ...


class Speaker(Protocol):
    def speak(self, text: str) -> None: ...          # enqueue; must not block
    async def drain(self) -> None: ...               # wait until everything queued is spoken
    def stop(self) -> None: ...                      # cut playback immediately


class Pointer(Protocol):
    def set_state(self, state: str, detail: str = "") -> None: ...
    def point(self, x: float, y: float, label: str) -> None: ...   # global top-left points
    def release(self) -> None: ...                   # return to the user's cursor


class Capturer(Protocol):
    def screens(self) -> list[ScreenInfo]: ...
    def capture(self, *, only_cursor_screen: bool = False) -> list[Screenshot]: ...


@dataclass(frozen=True)
class Route:
    """What the fast router decided before the slow model runs."""

    needs_screen: bool = True
    intent: str = "explain"          # point | explain | answer | chat
    cursor_screen_only: bool = False # multi-display: send just the screen under the cursor
    detailed: bool = False           # walkthrough / deeper explanation wanted
    provider: str = "default"
    confidence: float = 0.0
    latency_ms: float = 0.0


class Router(Protocol):
    async def route(self, transcript: str, screens: list[ScreenInfo]) -> Route: ...


@dataclass(frozen=True)
class Target:
    """Where the buddy should land, in global top-left points."""

    x: float
    y: float
    label: str
    screen: int
    element: Rect | None = None      # snapped UI element bounds, when known
    source: str = "model"            # model | snapped


class Snapper(Protocol):
    async def snap(self, target: Target) -> Target: ...


# -- results ------------------------------------------------------------------

@dataclass
class TurnResult:
    transcript: str
    spoken: str = ""
    targets: list[Target] = field(default_factory=list)
    route: Route = field(default_factory=Route)
    state: str = "done"              # done | cancelled | error
    error: str = ""
    timings: dict[str, float] = field(default_factory=dict)


class _NullPointer:
    def set_state(self, state: str, detail: str = "") -> None: ...
    def point(self, x: float, y: float, label: str) -> None: ...
    def release(self) -> None: ...


class _NullSpeaker:
    def speak(self, text: str) -> None: ...
    async def drain(self) -> None: ...
    def stop(self) -> None: ...


def resolve_target(tag: PointTag, shots: list[Screenshot]) -> Target | None:
    """Map a tag's screenshot pixels to global points on the right display."""
    if not shots:
        return None
    if tag.screen is not None:
        shot = next((item for item in shots if item.screen.index == tag.screen), None)
        if shot is None:
            return None
    else:
        shot = next((item for item in shots if item.screen.is_cursor_screen), shots[0])
    if not (0 <= tag.x <= shot.width and 0 <= tag.y <= shot.height):
        return None
    x, y = shot.to_global(tag.x, tag.y)
    return Target(x=x, y=y, label=tag.label, screen=shot.screen.index)


# -- the loop -----------------------------------------------------------------

class Companion:
    def __init__(self, *, brain: Brain, capturer: Capturer, speaker: Speaker | None = None,
                 pointer: Pointer | None = None, router: Router | None = None,
                 snapper: Snapper | None = None, conversation: Conversation | None = None,
                 system_prompt: str = SYSTEM_PROMPT, clock: Callable[[], float] = time.perf_counter):
        self.brain = brain
        self.capturer = capturer
        self.speaker = speaker or _NullSpeaker()
        self.pointer = pointer or _NullPointer()
        self.router = router
        self.snapper = snapper
        self.conversation = conversation or Conversation()
        self.system_prompt = system_prompt
        self.clock = clock
        self._task: asyncio.Task | None = None
        self._prefetched: tuple[float, concurrent.futures.Future] | None = None
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="buddy-capture")

    def prefetch(self) -> None:
        """Start the screenshot at key release, while speech is still being finalized.

        Safe to call from any thread. A prefetch older than a few seconds is
        ignored, so a stale screen never answers a new question.
        """
        self._prefetched = (self.clock(), self._pool.submit(self.capturer.capture))

    def _take_prefetch(self, max_age: float = 4.0):
        prefetched, self._prefetched = self._prefetched, None
        if prefetched is None or self.clock() - prefetched[0] > max_age:
            return None
        return prefetched[1]

    # The app calls these from its event loop thread.
    def interrupt(self) -> None:
        """Push-to-talk pressed again: stop talking and pointing right away."""
        if self._task and not self._task.done():
            self._task.cancel()
        self.speaker.stop()
        self.pointer.release()

    async def respond(self, transcript: str) -> TurnResult:
        self.interrupt()
        self._task = asyncio.ensure_future(self._respond(transcript))
        try:
            return await self._task
        except asyncio.CancelledError:
            return TurnResult(transcript=transcript, state="cancelled")

    async def _respond(self, transcript: str) -> TurnResult:
        transcript = " ".join(transcript.split())
        result = TurnResult(transcript=transcript)
        started = self.clock()

        def mark(name: str) -> None:
            result.timings[name] = round((self.clock() - started) * 1000, 1)

        if not transcript:
            result.state = "error"
            result.error = "I didn't catch that."
            self.pointer.set_state("idle")
            return result

        self.pointer.set_state("thinking")
        try:
            shots, result.route = await self._look(transcript)
            mark("looked")
            history = self.conversation.history()
            turn = Turn("user", user_turn_text(transcript, shots), images=tuple(shots))
            reply = ReplyStream()
            first = True
            async for delta in self.brain.stream(system=self.system_prompt, turns=[*history, turn],
                                                 detailed=result.route.detailed):
                if first:
                    mark("first_token")
                    first = False
                for event in reply.feed(delta):
                    await self._handle(event, shots, result, mark)
            for event in reply.close():
                await self._handle(event, shots, result, mark)
            mark("model_done")
            result.spoken = reply.spoken_text
            self.conversation.record(transcript, _history_text(reply, result.targets))
            await self.speaker.drain()
            mark("spoken")
        except asyncio.CancelledError:
            self.speaker.stop()
            self.pointer.release()
            raise
        except Exception as exc:  # surfaced to the user, never silently swallowed
            result.state = "error"
            result.error = _friendly_error(exc)
            self.speaker.stop()
            self.speaker.speak(result.error)
            await self.speaker.drain()
        finally:
            self.pointer.set_state("idle")
        return result

    async def _look(self, transcript: str) -> tuple[list[Screenshot], Route]:
        """Route and capture concurrently; drop the screenshots if not needed."""
        prefetched = self._take_prefetch()
        capture = (asyncio.wrap_future(prefetched) if prefetched is not None
                   else asyncio.create_task(asyncio.to_thread(self.capturer.capture)))
        route = Route()
        if self.router is not None:
            try:
                screens = await asyncio.to_thread(self.capturer.screens)
                route = await self.router.route(transcript, screens)
            except Exception:
                route = Route(provider="fallback")
        shots = await capture
        if not route.needs_screen:
            return [], route
        if route.cursor_screen_only and len(shots) > 1:
            shots = [shot for shot in shots if shot.screen.is_cursor_screen] or shots[:1]
        return shots, route

    async def _handle(self, event, shots, result: TurnResult, mark) -> None:
        if isinstance(event, SpeechChunk):
            if "first_speech" not in result.timings:
                mark("first_speech")
                self.pointer.set_state("speaking")
            self.speaker.speak(event.text)
        elif isinstance(event, PointTag):
            target = resolve_target(event, shots)
            if target is None:
                return
            if self.snapper is not None:
                try:
                    target = await self.snapper.snap(target)
                except Exception:
                    pass
            result.targets.append(target)
            if "first_point" not in result.timings:
                mark("first_point")
            self.pointer.point(target.x, target.y, target.label)


def _history_text(reply: ReplyStream, targets: list[Target]) -> str:
    """What the assistant 'said' last turn, including where it pointed."""
    text = reply.spoken_text
    if targets:
        where = "; ".join(f"{t.label or 'here'} on screen{t.screen}" for t in targets)
        text += f" (pointed at: {where})"
    return text


def _friendly_error(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    lowered = message.lower()
    if "api key" in lowered or "401" in lowered or "authentication" in lowered:
        return "I can't reach my model. Check the API key in your settings."
    if "429" in lowered or "rate" in lowered:
        return "I'm being rate limited right now. Give me a moment and try again."
    if "timeout" in lowered or "timed out" in lowered:
        return "That took too long. Try asking again."
    return "Something went wrong on my end. Try asking again."
