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
from mcp_vision.buddy.pointing import ActionTag, DoneTag, PlanTag, PointTag, ReplyStream, SpeechChunk, StepsTag
from mcp_vision.buddy.prompt import (
    ACTION_FOLLOWUP, GUIDE_FOLLOWUP, LOOK_FOLLOWUP, SYSTEM_PROMPT, system_prompt, user_turn_text,
)
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
    did: list[str] = field(default_factory=list)          # actions carried out, for history and UI
    reports: list[str] = field(default_factory=list)      # action results the model should see next
    look_after: float | None = None  # an action wants a fresh look (e.g. a page loading)
    pending: str = ""                # an action is waiting for the user's yes
    plan: tuple[str, ...] = ()


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
                 walkthroughs: bool = True, guide_timeout: float = 90.0, max_guide_turns: int = 10,
                 actions: Any = None, notes: Callable[[], str] | None = None, max_followups: int = 3,
                 routines: Any = None, capture_timeout: float = 6.0):
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
        self.actions = actions
        self.notes = notes
        self.routines = routines
        self.capture_timeout = capture_timeout
        self.max_followups = max_followups
        self._loop: asyncio.AbstractEventLoop | None = None
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
        return await self._own(self._session(transcript), transcript)

    async def answer_pending(self, accept: bool) -> TurnResult | None:
        """The island's Confirm / Cancel buttons for a waiting action."""
        if self.actions is None or self.actions.pending is None:
            return None
        return await self._own(self._answer(accept, "yes" if accept else "no"), "")

    async def _own(self, coroutine, transcript: str) -> TurnResult:
        self._loop = asyncio.get_running_loop()
        self._stop_current()
        self._task = asyncio.ensure_future(coroutine)
        try:
            return await self._task
        except asyncio.CancelledError:
            return TurnResult(transcript=transcript, state="cancelled")

    async def _session(self, transcript: str) -> TurnResult:
        if self.actions is not None and self.actions.pending is not None:
            from mcp_vision.buddy.actions import answer_kind

            kind = answer_kind(transcript)
            if kind:
                answered = await self._answer(kind == "yes", transcript)
                if answered is not None:
                    return answered
            else:
                self.actions.cancel_pending()          # they moved on to something else
                self.emit("confirm", cleared=True)
        routine = self.routines.match(transcript) if self.routines is not None and self.actions is not None else None
        if routine is not None:
            return await self._run_routine(routine, transcript)
        result = await self._turn(transcript)
        turns, followups = 1, 0
        while result.state == "done" and (result.reports or result.look_after) and followups < self.max_followups:
            if result.look_after:
                self.emit("step", id="wait", label="Waiting for it to load", status="active")
                await asyncio.sleep(result.look_after)
            prompt = (LOOK_FOLLOWUP if result.look_after else ACTION_FOLLOWUP).format(
                reports="\n".join(f"- {report}" for report in result.reports) or "- (nothing else)")
            followup = await self._turn(prompt, guide=True, screen=bool(result.look_after))
            turns += 1
            followups += 1
            followup.did = result.did + followup.did
            followup.plan = followup.plan or result.plan
            followup.steps_total = followup.steps_total or result.steps_total
            if followup.state != "done":
                return followup
            result = followup
        total = result.steps_total
        done_steps = 1
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

    async def _answer(self, accept: bool, transcript: str) -> TurnResult | None:
        outcome = await self.actions.answer(accept)
        self.emit("confirm", cleared=True)
        if outcome is None:
            return None
        result = TurnResult(transcript=transcript)
        if outcome.status == "done":
            text = (outcome.result.say if outcome.result else "") or "Done."
            result.did.append(outcome.label)
            self.emit("step", id="action-confirmed", label=outcome.label, status="done",
                      detail=outcome.result.detail if outcome.result else "")
            self.emit("action", name=outcome.spec.name, status="done", label=outcome.label,
                      detail=outcome.result.detail if outcome.result else "",
                      items=outcome.result.items if outcome.result else [])
        else:
            text = outcome.message or "Okay."
            if outcome.status == "failed":
                result.state = "error"
                result.error = text
                self.emit("step", id="action-confirmed", label=outcome.label, status="failed", detail=text)
        self.emit("phase", phase="answering")
        self.speaker.speak(text)
        self.emit("answer", text=text)
        result.spoken = text
        self.conversation.record(transcript or ("yes" if accept else "no"),
                                 text + (f" (did: {'; '.join(result.did)})" if result.did else ""))
        self.emit("done", latency_ms=None, spoken=text)
        await self.speaker.drain()
        return result

    async def _run_routine(self, routine, transcript: str) -> TurnResult:
        """A taught phrase: run it right away, no model call."""
        result = TurnResult(transcript=transcript)
        self.emit("phase", phase="thinking", transcript=transcript, guide=False)
        self.emit("step", id="routine", label=f"Routine: {routine.name}", status="active")
        outcome = await self.actions.handle("run_routine", {"name": routine.name})
        if outcome.status == "done":
            text = outcome.result.say or f"Running {routine.name}."
            result.did.append(f"ran {routine.name}")
            self.emit("step", id="routine", label=f"Routine: {routine.name}", status="done",
                      detail=outcome.result.detail)
        else:
            text = outcome.message
            result.state, result.error = "error", outcome.message
            self.emit("step", id="routine", label=f"Routine: {routine.name}", status="failed", detail=text)
        self.emit("phase", phase="answering")
        self.emit("answer", text=text)
        self.speaker.speak(text)
        result.spoken = text
        self.conversation.record(transcript, f"{text} (did: ran the {routine.name} routine)")
        self.emit("done", latency_ms=None, spoken=text)
        await self.speaker.drain()
        return result

    # -- timers and other late announcements (called from worker threads) ------------------
    def _schedule(self, delay: float, fn: Callable[[], None]) -> None:
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.call_later, delay, fn)

    def announce(self, text: str) -> None:
        loop = self._loop
        if loop is None:
            return

        def say() -> None:
            self.emit("notice", text=text)
            self.speaker.speak(text)
        loop.call_soon_threadsafe(say)

    def _notes(self) -> str:
        import datetime as _dt

        now = _dt.datetime.now().strftime("%A, %B %d %Y, %I:%M %p").replace(" 0", " ")
        extra = ""
        if self.notes is not None:
            try:
                extra = self.notes() or ""
            except Exception:
                extra = ""
        return f"now: {now}" + (f"\n{extra}" if extra else "")

    async def _turn(self, transcript: str, *, guide: bool = False, screen: bool | None = None) -> TurnResult:
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
            shots, context, result.route = await self._look(transcript, result, screen)
            mark("looked")
            self._shots, self._context = shots, context
            history = self.conversation.history()
            text = user_turn_text(transcript, shots, context, vision=self.vision, notes=self._notes())
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
            result.plan = reply.plan or result.plan
            if result.pending and "?" not in result.spoken:
                ask = f"{result.pending}. Say yes and I'll do it."
                self.speaker.speak(ask)
                self.emit("answer", text=(" " if result.spoken else "") + ask)
                result.spoken = (result.spoken + " " + ask).strip()
            self.conversation.record(transcript, _history_text(reply, result.targets, result.did))
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

    async def _look(self, transcript: str, result: TurnResult,
                    screen: bool | None = None) -> tuple[list[Screenshot], ScreenContext | None, Route]:
        """Route, capture, and read screen context concurrently."""
        if screen is False:                     # e.g. handing search results back: no need to look
            return [], None, Route(needs_screen=False, provider="followup")
        started = self.clock()
        prefetched = self._take_prefetch()
        capture = (asyncio.wrap_future(prefetched) if prefetched is not None
                   else asyncio.create_task(asyncio.to_thread(self.capturer.capture)))
        context_task = (asyncio.create_task(asyncio.to_thread(self.context.snapshot))
                        if self.context is not None else None)
        route = Route()
        if self.router is not None:
            try:
                screens = await asyncio.wait_for(asyncio.to_thread(self.capturer.screens), self.capture_timeout)
                route = await self.router.route(transcript, screens)
            except Exception:
                route = Route(provider="fallback")
            self.emit("step", id="route", status="done", detail=f"{route.latency_ms:.0f}ms",
                      label=f"{'Jev' if route.provider == 'jev' else 'Rules'}: "
                            f"{'needs screen' if route.needs_screen else 'no screen needed'}")
        try:
            shots = await asyncio.wait_for(capture, self.capture_timeout)
        except Exception:                       # includes a screen grab that never returns
            # No display, or Screen Recording not granted yet: answer without the screen.
            shots = []
            if route.needs_screen:
                self.emit("step", id="look", label="Couldn't see your screen", status="skipped",
                          detail="check Screen Recording")
        context: ScreenContext | None = None
        if context_task is not None:
            try:
                context = await asyncio.wait_for(context_task, 0.6)
            except Exception:
                context = None
        if screen:
            route = Route(needs_screen=True, intent=route.intent, detailed=route.detailed, provider=route.provider,
                          confidence=route.confidence, latency_ms=route.latency_ms)
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
        elif isinstance(event, PlanTag):
            result.plan = event.steps
            self.emit("plan", steps=list(event.steps))
        elif isinstance(event, ActionTag):
            await self._act(event, result)
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


    async def _act(self, tag: ActionTag, result: TurnResult) -> None:
        if self.actions is None:
            return
        self.actions.ctx.schedule = self._schedule
        self.actions.ctx.announce = self.announce
        self.actions.ctx.screen = (getattr(self, "_shots", []), getattr(self, "_context", None))
        self._action_seq = getattr(self, "_action_seq", 0) + 1
        step_id = f"action-{self._action_seq}"
        spec = self.actions.specs.get(tag.name)
        label = spec.describe(tag.args) if spec else tag.name.replace("_", " ")
        self.emit("step", id=step_id, label=label, status="active")
        outcome = await self.actions.handle(tag.name, tag.args)
        if outcome.status == "pending":
            preview = outcome.preview
            result.pending = preview.title
            self.emit("step", id=step_id, label=label, status="done", detail="waiting for your OK")
            self.emit("confirm", title=preview.title, lines=preview.lines, confirm=preview.confirm, name=tag.name)
            return
        if outcome.status == "done":
            action_result = outcome.result
            result.did.append(label)
            if action_result.report:
                result.reports.append(f"{tag.name}: {action_result.report}")
            if action_result.look_after:
                result.look_after = max(result.look_after or 0.0, action_result.look_after)
            self.emit("step", id=step_id, label=label, status="done", detail=action_result.detail)
            self.emit("action", name=tag.name, status="done", label=label, detail=action_result.detail,
                      items=action_result.items)
            if action_result.say:
                self.speaker.speak(action_result.say)
                self.emit("answer", text=" " + action_result.say)
            return
        # failed, unknown, disabled: say why, and let the model know if it gets another turn
        self.emit("step", id=step_id, label=label, status="failed", detail=outcome.message)
        self.emit("action", name=tag.name, status="failed", label=label, detail=outcome.message)
        self.speaker.speak(outcome.message)
        self.emit("answer", text=" " + outcome.message)


def _history_text(reply: ReplyStream, targets: list[Target], did: list[str] | None = None) -> str:
    """What the assistant 'said' last turn, including where it pointed and what it did."""
    text = reply.spoken_text
    if reply.steps:
        text = f"[STEPS:{reply.steps}] " + text
    if targets:
        where = "; ".join(f"{t.label or 'here'} on screen{t.screen}" for t in targets)
        text += f" (pointed at: {where})"
    if did:
        text += f" (did: {'; '.join(did)})"
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
