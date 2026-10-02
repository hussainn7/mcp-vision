"""Plip's turn loop: hear -> look -> think -> talk and point -> (guide).

Everything platform-specific (overlay windows, microphones, speakers, the
model provider) is injected through small ports, so this module runs and is
tested without a display or network.

A turn: route (Jev/rules), screenshots, and screen context run in parallel;
the brain streams a reply that becomes speech per sentence and pointer
flights per tag. If the reply opens a walkthrough (``[STEPS:n]``), Plip
waits for the screen to change after each step, looks again, and continues
until ``[DONE]``, a timeout, or the user presses the shortcut.

``observer(kind, data)`` receives UI events: phase, step, engine, answer,
point, walkthrough, done, error.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from mcp_vision.buddy.conversation import Conversation, Turn
from mcp_vision.buddy.geometry import Rect, ScreenInfo, Screenshot
from mcp_vision.buddy.pointing import DoneTag, PointTag, ReplyStream, SpeechChunk, StepsTag
from mcp_vision.buddy.prompt import GUIDE_FOLLOWUP, SYSTEM_PROMPT, system_prompt, user_turn_text
from mcp_vision.buddy.screen_context import ScreenContext

Observer = Callable[[str, dict[str, Any]], None]


# -- ports --------------------------------------------------------------------

class Brain(Protocol):
    """A streaming model. ``vision`` says whether it accepts screenshots."""

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


class ContextSource(Protocol):
    def snapshot(self) -> ScreenContext: ...


class Watcher(Protocol):
    async def wait_for_change(self, timeout: float) -> bool: ...


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
    """Where Plip should land, in global top-left points."""

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
    steps_total: int | None = None   # walkthrough length announced by [STEPS:n]
    finished: bool = False           # [DONE] seen
    turns: int = 1                   # model turns in this session (walkthroughs > 1)


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


def brain_badge(brain: Any) -> dict[str, Any]:
    return {"label": getattr(brain, "label", None) or getattr(brain, "name", "model"),
            "kind": getattr(brain, "kind", "api"), "model": getattr(brain, "model", None)}


# -- the loop -----------------------------------------------------------------

class Companion:
    def __init__(self, *, brain: Brain, capturer: Capturer, speaker: Speaker | None = None,
                 pointer: Pointer | None = None, router: Router | None = None,
                 snapper: Snapper | None = None, conversation: Conversation | None = None,
                 system_prompt: str | None = None, clock: Callable[[], float] = time.perf_counter,
                 snap_timeout: float = 0.6, context: ContextSource | None = None,
                 observer: Observer | None = None, watcher: Watcher | None = None,
                 walkthroughs: bool = True, guide_timeout: float = 90.0, max_guide_turns: int = 10):
        self.brain = brain
        self.capturer = capturer
        self.speaker = speaker or _NullSpeaker()
        self.pointer = pointer or _NullPointer()
        self.router = router
        self.snapper = snapper
        self.conversation = conversation or Conversation()
        self._system_prompt = system_prompt
        self.clock = clock
        self.snap_timeout = snap_timeout
        self.context = context
        self.observer = observer
        self.watcher = watcher
        self.walkthroughs = walkthroughs
        self.guide_timeout = guide_timeout
        self.max_guide_turns = max_guide_turns
        self._task: asyncio.Task | None = None
        self._token: int | None = None
        self._prefetched: tuple[float, concurrent.futures.Future] | None = None
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="buddy-look")

    @property
    def vision(self) -> bool:
        return bool(getattr(self.brain, "vision", True))

    @property
    def system_prompt(self) -> str:
        return self._system_prompt or system_prompt(vision=self.vision)

    def emit(self, event: str, /, **data: Any) -> None:
        if self.observer is not None:
            try:
                self.observer(event, data)
            except Exception:
                pass          # the UI must never break a turn

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

    # The app calls these on the companion's event loop thread.
    def interrupt(self, token: int | None = None) -> None:
        """Push-to-talk pressed again: stop talking, pointing, and guiding right away.

        ``token`` identifies the press. A turn submitted for an older press is
        dropped even if it had not started yet when this interrupt ran.
        """
        if token is not None:
            self._token = token
        self._stop_current()

    def _stop_current(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self.speaker.stop()
        self.pointer.release()

    async def respond(self, transcript: str, token: int | None = None) -> TurnResult:
        if token is not None and token != self._token:
            return TurnResult(transcript=transcript, state="cancelled")
        self._stop_current()
        self._task = asyncio.ensure_future(self._session(transcript))
        try:
            return await self._task
        except asyncio.CancelledError:
            return TurnResult(transcript=transcript, state="cancelled")

    async def _session(self, transcript: str) -> TurnResult:
        result = await self._turn(transcript)
        total = result.steps_total
        done_steps = 1
        turns = 1
        while (self.walkthroughs and self.watcher is not None and result.state == "done" and total
               and not result.finished and done_steps < total + 2 and turns < self.max_guide_turns):
            label = result.targets[-1].label if result.targets else ""
            self.emit("walkthrough", index=done_steps - 1, total=total, label=label, waiting=True)
            if not await self.watcher.wait_for_change(self.guide_timeout):
                self.emit("walkthrough", index=done_steps - 1, total=total, label=label, waiting=False, timed_out=True)
                break
            followup = await self._turn(GUIDE_FOLLOWUP.format(done=done_steps, total=total), guide=True)
            turns += 1
            if followup.state != "done":
                return followup
            done_steps += 1
            total = max(total, followup.steps_total or total)
            followup.steps_total = total
            followup.turns = turns
            result = followup
        if total and result.finished:
            self.emit("walkthrough", index=total - 1, total=total, label="done", waiting=False, finished=True)
        result.turns = turns
        return result

    async def _turn(self, transcript: str, *, guide: bool = False) -> TurnResult:
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
        self.emit("phase", phase="thinking", transcript="" if guide else transcript, guide=guide)
        cancelled = False
        try:
            shots, context, result.route = await self._look(transcript, result)
            mark("looked")
            history = self.conversation.history()
            text = user_turn_text(transcript, shots, context, vision=self.vision)
            turn = Turn("user", text, images=tuple(shots) if self.vision else ())
            reply = ReplyStream()
            badge = brain_badge(self.brain)
            self.emit("engine", **badge)
            self.emit("step", id="think", label=f"{badge['label']} is thinking", status="active")
            first = True
            async for delta in self.brain.stream(system=self.system_prompt, turns=[*history, turn],
                                                 detailed=result.route.detailed):
                if first:
                    mark("first_token")
                    self.emit("step", id="think", label=f"{badge['label']} answered", status="done",
                              detail=f"{result.timings['first_token'] / 1000:.1f}s")
                    first = False
                for event in reply.feed(delta):
                    await self._handle(event, shots, result, mark)
            for event in reply.close():
                await self._handle(event, shots, result, mark)
            mark("model_done")
            result.spoken = reply.spoken_text
            result.finished = reply.done
            result.steps_total = reply.steps
            self.conversation.record(transcript, _history_text(reply, result.targets))
            self.emit("done", latency_ms=result.timings.get("first_speech"), spoken=result.spoken)
            await self.speaker.drain()
            mark("spoken")
        except asyncio.CancelledError:
            # A new press owns the overlay now (it is already "listening");
            # don't stomp on it with "idle" from this abandoned turn.
            cancelled = True
            self.speaker.stop()
            self.pointer.release()
            raise
        except Exception as exc:  # surfaced to the user, never silently swallowed
            result.state = "error"
            result.error = _friendly_error(exc)
            self.emit("error", message=result.error)
            self.speaker.stop()
            self.speaker.speak(result.error)
            await self.speaker.drain()
        finally:
            if not cancelled:
                self.pointer.set_state("idle")
        return result

    async def _look(self, transcript: str, result: TurnResult) -> tuple[list[Screenshot], ScreenContext | None, Route]:
        """Route, capture, and read screen context concurrently."""
        started = self.clock()
        prefetched = self._take_prefetch()
        capture = (asyncio.wrap_future(prefetched) if prefetched is not None
                   else asyncio.create_task(asyncio.to_thread(self.capturer.capture)))
        context_task = (asyncio.create_task(asyncio.to_thread(self.context.snapshot))
                        if self.context is not None else None)
        route = Route()
        if self.router is not None:
            try:
                screens = await asyncio.to_thread(self.capturer.screens)
                route = await self.router.route(transcript, screens)
            except Exception:
                route = Route(provider="fallback")
            self.emit("step", id="route", status="done", detail=f"{route.latency_ms:.0f}ms",
                      label=f"{'Jev' if route.provider == 'jev' else 'Rules'}: "
                            f"{'needs screen' if route.needs_screen else 'no screen needed'}")
        shots = await capture
        context: ScreenContext | None = None
        if context_task is not None:
            try:
                context = await asyncio.wait_for(context_task, 0.6)
            except Exception:
                context = None
        if not route.needs_screen:
            shots, context = [], None
        elif route.cursor_screen_only and len(shots) > 1:
            shots = [shot for shot in shots if shot.screen.is_cursor_screen] or shots[:1]
        elapsed = (self.clock() - started) * 1000
        if shots:
            label = f"Looked at {len(shots)} screen{'s' if len(shots) > 1 else ''}"
            if context and context.app:
                label += f" · {context.app}"
            self.emit("step", id="look", label=label, status="done", detail=f"{elapsed:.0f}ms")
        if context and context.controls:
            self.emit("step", id="map", label=f"Mapped {min(len(context.controls), 70)} controls", status="done")
        return shots, context, route

    async def _handle(self, event, shots, result: TurnResult, mark) -> None:
        if isinstance(event, SpeechChunk):
            first = "first_speech" not in result.timings
            if first:
                mark("first_speech")
                self.pointer.set_state("speaking")
                self.emit("phase", phase="answering")
            self.speaker.speak(event.text)
            self.emit("answer", text=event.text if first else " " + event.text)
        elif isinstance(event, StepsTag):
            result.steps_total = event.total
            self.emit("walkthrough", index=0, total=event.total, label="", waiting=False)
        elif isinstance(event, DoneTag):
            result.finished = True
        elif isinstance(event, PointTag):
            target = resolve_target(event, shots)
            if target is None:
                return
            if self.snapper is not None:
                try:
                    # Snapping refines the point; it must not hold up the reply stream.
                    target = await asyncio.wait_for(self.snapper.snap(target), self.snap_timeout)
                except Exception:
                    pass
            result.targets.append(target)
            if "first_point" not in result.timings:
                mark("first_point")
            self.pointer.point(target.x, target.y, target.label)
            self.emit("point", x=target.x, y=target.y, label=target.label, screen=target.screen,
                      snapped=target.source == "snapped")
            self.emit("step", id=f"point-{len(result.targets)}", status="done",
                      label=f"Pointed at {target.label or 'it'}", detail="snapped" if target.source == "snapped" else "")


def _history_text(reply: ReplyStream, targets: list[Target]) -> str:
    """What the assistant 'said' last turn, including where it pointed."""
    text = reply.spoken_text
    if reply.steps:
        text = f"[STEPS:{reply.steps}] " + text
    if targets:
        where = "; ".join(f"{t.label or 'here'} on screen{t.screen}" for t in targets)
        text += f" (pointed at: {where})"
    if reply.done:
        text += " [DONE]"
    return text


def _friendly_error(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    lowered = message.lower()
    if "not logged in" in lowered or "login" in lowered or "sign in" in lowered:
        return "I need you to sign in to my brain first. Open my settings and pick a brain."
    if "not installed" in lowered or "no such file" in lowered:
        return "My brain app isn't installed. Open my settings and pick another one."
    if "api key" in lowered or "401" in lowered or "authentication" in lowered:
        return "I can't reach my model. Check the API key in your settings."
    if "waiting for network" in lowered or "connection failed" in lowered or "network" in lowered:
        return "I can't reach my brain right now. Check your internet connection and try again."
    if "429" in lowered or "rate" in lowered or "usage limit" in lowered or "quota" in lowered:
        return "I'm being rate limited right now. Give me a moment and try again."
    if "timeout" in lowered or "timed out" in lowered:
        return "That took too long. Try asking again."
    return "Something went wrong on my end. Try asking again."


__all__ = ["SYSTEM_PROMPT", "Brain", "Companion", "Route", "Target", "TurnResult", "resolve_target", "brain_badge"]
