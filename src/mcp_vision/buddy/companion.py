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
import logging
import hashlib
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from mcp_vision.buddy.conversation import Conversation, Turn
from mcp_vision.buddy.geometry import Rect, ScreenInfo, Screenshot
from mcp_vision.buddy.pointing import (
    ActionTag, DoneTag, GoalTag, PlanTag, PointTag, ReplyStream, SpeechChunk, StepsTag,
)
from mcp_vision.buddy.prompt import (
    ACTION_FOLLOWUP, AGENT_FOLLOWUP, LOOK_FOLLOWUP, SYSTEM_PROMPT, guide_followup, system_prompt, user_turn_text,
)
from mcp_vision.buddy.screen_context import ScreenContext
from mcp_vision.buddy.usage import Request, Usage, estimate, image_tokens, text_tokens
from mcp_vision.buddy.workers import PooledExecutor

log = logging.getLogger("mcp_vision.buddy.companion")

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
    multistep: bool = False          # takes several actions ("open X and do Y")
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
    failed: list[str] = field(default_factory=list)       # actions that didn't work
    farewell: bool = False           # "thanks, bye": the session wrapped up
    outcome: str = ""                # how the request ended (usage.OUTCOMES), set when it's over
    usage: Usage | None = None       # tokens the whole request used (as the brain reported, or estimated)
    goal: str = ""                   # the multi-step task ([GOAL: ...])
    acted: list[str] = field(default_factory=list)        # this turn's action results, failures too
    settle: float | None = None      # max wait for the screen to settle after an action
    hands: bool = False              # Plip clicked, scrolled or typed on screen
    opened: str = ""                 # what the last action opened (a link, a page)
    verified: bool | None = None     # [DONE] after acting: did the screen confirm it?
    hushed: bool = False             # a step failed: the rest assumed it worked
    said: int = 0                    # chars spoken this turn (speech budget)
    held: bool = False               # stopped reading aloud; the rest is on screen
    typed: list[str] = field(default_factory=list)        # text typed into fields: not a screen change
    muted: dict[int, str] = field(default_factory=dict)  # chunk index -> what's said instead ("" = nothing)
    heard: int = 0                   # chunks streamed so far


@dataclass
class _Meter:
    """What one request (a question, or a whole task) costs, across every model call it takes."""

    started: float
    usage: Usage = field(default_factory=Usage)
    turns: int = 0
    actions: list[str] = field(default_factory=list)
    first: float | None = None       # seconds to the first spoken word
    model: float = 0.0               # seconds waiting on the brain
    act: float = 0.0                 # seconds carrying out actions
    settle: float = 0.0              # seconds waiting for the screen between steps

    def timings(self) -> dict[str, int]:
        first = 0 if self.first is None else max(1, int(self.first * 1000))     # 0 only when it never spoke
        return {"first_ms": first, "model_ms": int(self.model * 1000),
                "act_ms": int(self.act * 1000), "settle_ms": int(self.settle * 1000)}


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
                 max_agent_steps: int = 15, step_effort: str | None = "low",
                 capture_timeout: float = 6.0, usage: Any = None, usage_kind: str = "voice",
                 settle_interval: float = 0.15):
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
        self.capture_timeout = capture_timeout
        self.max_followups = max_followups
        self.max_agent_steps = max_agent_steps        # step budget for on-screen loops and goals
        self.step_effort = step_effort                # effort for routine task steps (None: the user's)
        self._goal = ""                               # open multi-step task, until [DONE]
        self._goal_waiting = False                    # the task stopped to ask the user something
        self._paused = False                          # out of steps, asked to keep going
        self._rejected = 0                            # [DONE]s sent back this task
        self._extra_note = ""                         # one-turn note for the model
        self._goal_route: Route | None = None         # the task's route, for keep going
        self._trail: list[str] = []                   # last few actions, to spot repeats
        self._hiccups = 0                             # steps that failed or went nowhere this task
        self._screens_seen: dict[str, int] = {}       # screen visits this task, to spot circling
        self._leaks = 0                               # tool calls written out as text this request
        self._asked = ""                              # the question Plip's last reply ended on
        self._consent = None                          # this request's yes: the step it names won't ask
        self._authored = False                        # Plip typed something they haven't seen yet
        self.settle_interval = settle_interval
        self.usage = usage                      # buddy.usage.UsageLog, or None to keep no record
        self.usage_kind = usage_kind
        self._meter: _Meter | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._token: int | None = None
        self._prefetched: tuple[float, concurrent.futures.Future] | None = None
        self._pool = PooledExecutor(max_workers=2, thread_name_prefix="buddy-look")

    @property
    def vision(self) -> bool:
        return bool(getattr(self.brain, "vision", True))

    @property
    def system_prompt(self) -> str:
        """Instructions + user notes (notes rarely change, so they ride in the cached prompt)."""
        base = self._system_prompt or system_prompt(vision=self.vision)
        known = ""
        if self.notes is not None:
            try:
                known = (self.notes() or "").strip()
            except Exception:
                known = ""
        return f"{base}\n\n{known}" if known else base

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
        if self.context is not None:                  # map too: a browser page walks in ~0.3 s
            self._mapped = (self.clock(), self._pool.submit(self._observe))

    def _route_effort(self, route: Route) -> str | None:
        """This route's call effort, so a warm process matches it."""
        effort_of = getattr(self.brain, "_effort", None)
        try:
            return effort_of(route.detailed) if effort_of is not None and route.detailed else None
        except Exception:
            return None

    def warm_brain(self, effort: str | None = None) -> None:
        """Start a brain process before the question exists. On the loop."""
        ensure = getattr(self.brain, "ensure_warm", None)
        if ensure is None or not getattr(self.brain, "prewarm", False):
            return
        try:
            ensure(self.system_prompt, effort)
        except Exception:
            pass                                      # a cold start is slower, not broken

    def _take_map(self, max_age: float = 4.0):
        """The map read at key release, if it's still fresh."""
        mapped, self._mapped = getattr(self, "_mapped", None), None
        if mapped is not None and self.clock() - mapped[0] <= max_age:
            return asyncio.wrap_future(mapped[1])
        return None

    def _take_prefetch(self, max_age: float = 4.0):
        prefetched, self._prefetched = self._prefetched, None
        if prefetched is None or self.clock() - prefetched[0] > max_age:
            return None
        return prefetched[1]

    # The app calls these on the companion's event loop thread.
    @property
    def busy(self) -> bool:
        """Working on a request (thinking, acting, or talking)."""
        return self._task is not None and not self._task.done()

    @property
    def unfinished(self) -> str:
        """The stopped, unfinished task for "Keep going", or ""."""
        return self._goal if self._goal and not self.busy else ""

    def hush(self) -> None:
        """Stop talking, keep working."""
        self.speaker.stop()

    def interrupt(self, token: int | None = None) -> None:
        """Push-to-talk pressed again: stop talking, pointing, and guiding right away.

        ``token`` identifies the press. A turn submitted for an older press is
        dropped even if it had not started yet when this interrupt ran.
        """
        if token is not None:
            self._token = token
        if self.actions is not None:
            self.actions.ctx.generation += 1          # stops a long scroll from the cut-off turn
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

    async def _drain(self) -> None:
        """Finish talking, then emit "quiet" (the island waits for the voice)."""
        await self.speaker.drain()
        self.emit("quiet")

    async def _own(self, coroutine, transcript: str) -> TurnResult:
        self._loop = asyncio.get_running_loop()
        self._stop_current()
        meter = self._meter = _Meter(started=time.time())
        self._task = asyncio.ensure_future(coroutine)
        try:
            result = await self._task
        except asyncio.CancelledError:
            result = TurnResult(transcript=transcript, state="cancelled")
        finally:
            self.emit("quiet")                        # nothing more will be said
        result.usage = meter.usage if meter.turns else None
        result.outcome = _outcome(result)
        result.timings.update(meter.timings())
        self._asked = _question(result.spoken) if result.state == "done" and not result.pending else ""
        offer = _offer(self._asked)
        if offer:
            self.emit("offer", text=offer)            # island shows Yes / No thanks
        self._consent, self._authored = None, False   # a yes lasts one request
        self._record(meter, result)
        self.emit("finished", outcome="bye" if result.farewell else result.outcome)
        return result

    @staticmethod
    def _meter_turn(meter: _Meter | None, used: Usage) -> None:
        """One model call, added to the meter of the request it belongs to."""
        if meter is not None:
            meter.usage = meter.usage + used
            meter.turns += 1

    def _record(self, meter: _Meter, result: TurnResult) -> None:
        """One line in usage.jsonl per request that reached the brain."""
        if self.usage is None or not meter.turns:
            return
        used, engine = meter.usage, str(getattr(self.brain, "name", "") or "")
        try:
            self.usage.add(Request(
                at=meter.started, engine=engine, label=brain_badge(self.brain)["label"], kind=self.usage_kind,
                model=used.model or str(getattr(self.brain, "model", "") or ""), input=used.input,
                output=used.output, cache_read=used.cache_read, cache_write=used.cache_write,
                cost=round(used.price(engine), 6), estimated=used.estimated, turns=meter.turns,
                actions=meter.actions[:40], outcome=result.outcome, goal=bool(result.goal) or meter.turns > 1,
                ms=int((time.time() - meter.started) * 1000), **meter.timings()))
            spent = meter.timings()
            log.info("request %s: first word %.1fs, brain %.1fs over %d turn%s, actions %.1fs, settling %.1fs",
                     result.outcome, spent["first_ms"] / 1000, spent["model_ms"] / 1000, meter.turns,
                     "" if meter.turns == 1 else "s", spent["act_ms"] / 1000, spent["settle_ms"] / 1000)
        except Exception:
            pass                                      # bookkeeping must never break a turn

    async def _session(self, transcript: str) -> TurnResult:
        from mcp_vision.buddy.actions import Consent

        asked, self._asked = self._asked, ""
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
        # a "yes" to Plip's question confirms the step it named
        self._consent = Consent.given(transcript, asked)
        if self.actions is not None:
            self.actions.ctx.state["said"] = transcript    # only their words can save or forget a fact
        waiting, self._goal_waiting = self._goal_waiting, False
        paused, self._paused = self._paused, False
        if FAREWELL_RE.match(transcript):
            return await self._wrap_up(transcript, _farewell(transcript))
        if self._goal and paused:
            from mcp_vision.buddy.actions import answer_kind

            kind = answer_kind(transcript)             # "want me to keep going?"
            if kind == "yes":
                return await self._resume(transcript)
            if kind == "no":
                self.emit("goal", text=self._goal, done=False, paused=True)
                return await self._wrap_up(transcript, "Okay, I'll leave it there.")
        if self._goal and CONTINUE_RE.match(transcript):
            return await self._resume(transcript)       # same steps resumed: a retype is still a repeat
        if self.actions is not None:
            # new user input: retyping earlier text isn't a repeat
            self.actions.ctx.state.pop("typed", None)
        if waiting and self._goal:
            # likely the answer to the task's question: carry on
            self._screens_seen = {}
            self._extra_note = (f"(you were working toward: {self._goal}, and stopped to ask them something. if this "
                                "answers it, carry on with the task; if they've moved on to something else, drop it.)")
            result = await self._turn(transcript)
            if not (result.acted or result.pending or result.finished or reply_asks(result.spoken)) \
                    and result.goal in {"", self._goal}:
                self._goal = ""                       # they moved on
            return await self._drive(result)
        self._goal = ""                               # a new request: any old task is over
        self._trail, self._hiccups, self._screens_seen, self._rejected, self._leaks = [], 0, {}, 0, 0
        result = await self._turn(transcript)
        if not self._goal and (result.acted or result.pending) and not result.finished and result.state == "done" \
                and (result.route.multistep or result.hands):
            # model forgot [GOAL] on a multi-step ask: use the request
            self._goal = result.goal = _clip(transcript, 160)
            self.emit("goal", text=self._goal, done=False)
        return await self._drive(result)

    async def _reply_only(self, transcript: str, text: str) -> TurnResult:
        """A canned reply, no model: said, shown, and recorded."""
        self.emit("phase", phase="answering")
        self.speaker.speak(text)
        self.emit("answer", text=text)
        self.conversation.record(transcript, text)
        self.emit("done", latency_ms=None, spoken=text)
        await self._drain()
        return TurnResult(transcript=transcript, spoken=text)

    async def _wrap_up(self, transcript: str, reply: str) -> TurnResult:
        """End the session: task, card and questions go; the conversation stays."""
        self._goal, self._goal_waiting, self._extra_note = "", False, ""
        self._trail, self._hiccups, self._screens_seen, self._rejected, self._leaks = [], 0, {}, 0, 0
        if self.actions is not None and self.actions.pending is not None:
            self.actions.cancel_pending()
            self.emit("confirm", cleared=True)
        result = await self._reply_only(transcript, reply)
        result.farewell = True
        return result

    def decline(self) -> None:
        """"No thanks" on the island's offer: also drops a paused task."""
        self._asked = ""
        if self._paused:
            self._paused, self._goal = False, ""

    async def _resume(self, transcript: str) -> TurnResult:
        """"Keep going": resume the goal with a fresh look and step budget."""
        self.emit("phase", phase="thinking", transcript=transcript, guide=False)
        self.emit("goal", text=self._goal, done=False)
        result = TurnResult(transcript=transcript, acted=[f"the user said: {transcript}"], goal=self._goal,
                            route=self._goal_route or Route())
        self._screens_seen = {}                       # fresh go: old visits don't count as circling
        return await self._drive(result)

    def _continues(self, result: TurnResult) -> bool:
        """Does this reply need another model turn on its own (no user input)?"""
        if result.state != "done" or result.pending:
            return False
        if result.finished and (result.hands or self._goal):
            return False
        if result.reports:
            return True                              # results it hasn't read yet
        if reply_asks(result.spoken):
            return False                             # it asked them something: wait
        if result.look_after:
            return True
        return bool(self._goal and result.acted)     # mid-goal and just acted: look again

    async def _drive(self, result: TurnResult, turns: int = 1) -> TurnResult:
        """Follow-up turns: results back, fresh looks, until the goal's [DONE]."""
        followups = 0
        goal = self._goal
        route = result.route                   # follow-ups reuse the route, not re-routed
        if self._goal:
            self._goal_route = route
        start = getattr(self, "_context", None)
        if self._goal and start is not None and not start.empty:     # the start screen counts as a visit
            self._screens_seen.setdefault(start.content_signature(), 1)
        limit = self.max_agent_steps
        while followups < (limit if (result.hands or self._goal) else self.max_followups):
            if result.finished and self._goal and result.state == "done" and not result.pending:
                problem = await self._check_done(result)
                if problem:
                    result.finished = False           # not done yet: the next step hears why
                    result.acted.append(problem)
                    self.emit("step", id=f"verify-{self._rejected}", label="That didn't take, fixing it",
                              status="failed")
            if not self._continues(result):
                break
            seen = getattr(self, "_context", None)
            changes = result.settle is not None
            if bool(self._goal) or result.hands:
                self.warm_brain(self._step_effort(result, result.acted) or self._route_effort(route))
            settled = await self._wait_for_screen(result)
            agent = bool(self._goal) or result.hands
            lines = list((result.acted if agent else result.reports) or ["(nothing else)"])
            after = getattr(self, "_settled", None)
            pixels, unchanged = None, None
            if agent and changes and settled is not None and seen is not None:
                unchanged = self._unchanged(settled, seen, result)
                # typing always moves pixels: they only count when nothing was typed
                if unchanged and not result.typed and (pixels := await self._pixels_moved()):
                    # map can't see it (unread web page, canvas); pixels can
                    lines.append("note: the screen changed, but not in the controls list, so here's a screenshot")
                    if self.actions is not None:
                        self.actions.ctx.state["force_image"] = True
                elif unchanged:
                    lines.append("note: nothing on screen changed after that step"           # costs nothing to notice
                                 + (" besides the text typed" if result.typed else ""))
                elif after is not None and after.content_signature() == seen.content_signature() \
                        and after.signature(values=False) != seen.signature(values=False):    # not just typing
                    lines.append("note: only the address or title changed; the page itself shows the same "
                                 "content as before, so it may not have loaded")
            moved = None if unchanged is None else not unchanged or bool(pixels)
            if agent and followups + 1 >= limit:
                if moved is None and seen is not None and not seen.empty:   # no settle to go by: look now
                    now = await asyncio.to_thread(self._observe)
                    moved = bool(now is not None and not now.empty and now.signature() != seen.signature()) or \
                        bool(await self._pixels_moved())
                # extra steps only when clearly progressing: moved, few stalls, no rejects, no circling
                circling = max(self._screens_seen.values(), default=0) >= 3
                if moved and limit < 2 * self.max_agent_steps and not self._rejected and not circling and \
                        self._hiccups <= 1 and not any(sign in line for line in lines for sign in _STUCK):
                    limit += 5                        # progressing: a few more steps, not a pause
                else:
                    lines.append("note: this is your last step before plip pauses for them. finish the goal if this "
                                 "step does it; otherwise say in a few words where you got to (plip tells them how to "
                                 "carry on). never hand them the remaining steps to do.")
            if any(sign in line for line in lines for sign in _STUCK):
                self._hiccups += 1
            reports = "\n".join(f"- {line}" for line in lines)
            template = AGENT_FOLLOWUP if agent else LOOK_FOLLOWUP if result.look_after else ACTION_FOLLOWUP
            toward = f" toward: {self._goal}" if self._goal else ""
            prompt = template.format(reports=reports, step=followups + 1, toward=toward)
            if self._goal:
                self.emit("goal", text=self._goal, done=False, step=followups + 1)
            # recorded short: the next step needs only the gist
            brief = f"(step {followups + 1}{toward}; results, not from the user) " + \
                "; ".join(_clip(line, 600) for line in lines)
            followup = await self._turn(prompt, guide=True, screen=agent or bool(result.look_after), route=route,
                                        record_as=_clip(brief, 1500), lean=agent,
                                        effort=self._step_effort(result, lines, moved) if agent else None)
            turns += 1
            followups += 1
            goal = self._goal or goal
            followup.hands = followup.hands or result.hands
            followup.did = result.did + followup.did
            followup.failed = result.failed + followup.failed
            followup.plan = followup.plan or result.plan
            followup.steps_total = followup.steps_total or result.steps_total
            followup.goal = goal
            if followup.state != "done":
                followup.turns = turns
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
            followup = await self._turn(guide_followup(done_steps, total, result.plan), guide=True,
                                        screen=True, route=route)
            turns += 1
            if followup.state != "done":
                return followup
            done_steps += 1
            total = max(total, followup.steps_total or total)
            followup.steps_total = total
            followup.plan = followup.plan or result.plan
            followup.turns = turns
            result = followup
        if total and result.finished:
            self.emit("walkthrough", index=total - 1, total=total, label="done", waiting=False, finished=True)
        if self._goal and result.finished:
            self.emit("goal", text=self._goal, done=True)
            self._goal = ""
            self.conversation.fold()                  # its step-by-step results needn't ride along any more
        elif self._goal and result.state == "done" and not self._continues(result) and not result.pending:
            self._goal_waiting = True                 # asked them something: their answer resumes it
        elif self._goal and self._continues(result):
            # out of steps: say so, keep the goal for "keep going"
            pause = "I'm not finished yet. Want me to keep going?"
            self._paused = True                       # a yes (said or tapped) resumes it
            self.speaker.speak(pause)
            self.emit("answer", text=" " + pause)
            self.emit("goal", text=self._goal, done=False, paused=True)
            result.spoken = (result.spoken + " " + pause).strip()
            result.outcome = "paused"
        elif not self._goal and turns > 1 and result.state == "done" and not result.pending:
            self.conversation.fold()
        result.goal = result.goal or goal
        result.route = route
        result.turns = turns
        await self._drain()                  # steps ran while it talked; let it finish
        return result

    def _step_effort(self, result: TurnResult, lines: list[str], moved: bool | None = None) -> str | None:
        """``step_effort`` for routine steps; None (the user's depth) when judging results or after trouble."""
        if not self.step_effort or moved is False:
            return None
        if any(report.split(":", 1)[0] in _READING for report in result.reports):
            return None                               # results to judge: full depth
        if self._rejected or self._leaks or any(" failed: " in line or line.startswith("note:") for line in lines):
            return None
        if max(self._screens_seen.values(), default=0) >= 2:
            return None                               # a revisited screen: think it through
        return self.step_effort

    async def _check_done(self, result: TurnResult) -> str:
        """[DONE] with an action: did it land? No model, twice a task max. Returns why not, or "" to accept."""
        if not result.acted:
            return ""                                 # read the results, then said done: trust it
        failed = [line for line in result.acted if " failed: " in line]
        if self._rejected >= 2:
            result.verified = False                   # stop looping, but don't call it done
            if failed:
                said = failed[-1].split(" failed: ", 1)[-1].split(" (", 1)[0].strip()
                self.speaker.speak(f"I couldn't finish that. {said}")
                self.emit("answer", text=f" I couldn't finish that. {said}")
            return ""
        problem = ""
        if failed:
            problem = (f"note: you ended with [DONE], but {failed[-1]}. the goal isn't met yet: fix it another way "
                       "(another way to aim, the keyboard, another control); only tell them what's in the way if it's "
                       "something only they can sort out.")
        elif result.opened and self._rejected == 0:
            # opening something isn't finishing it: one more look
            if result.settle:
                await self._settle(result.settle)
                result.settle = None
            problem = (f"note: you ended with [DONE] right after {result.opened.lower()}, which opens something "
                       "rather than finishing it. here's the screen now: finish the goal, or if it really is done, "
                       "say so and end with [DONE].")
        elif result.settle:
            seen = getattr(self, "_context", None)
            settled = await self._settle(result.settle)
            result.settle = None
            if settled is None or seen is None:
                result.verified = False               # no map to check against
            elif self._unchanged(settled, seen, result) and (result.typed or not await self._pixels_moved()):
                last = (result.did[-1] if result.did else "that step").lower()
                besides = " besides the text typed" if result.typed else ""
                problem = (f"note: you ended with [DONE] right after {last}, but nothing on screen changed{besides}, "
                           "so it probably didn't work. check and fix it another way; if it really can't be done, "
                           "tell them it didn't take.")
            else:
                result.verified = True
                self.emit("step", id="verify", label="Checked it worked", status="done")
        if problem:
            self._rejected += 1
        return problem

    def _unchanged(self, settled: str, seen: ScreenContext, result: TurnResult) -> bool:
        """Same screen the model saw? Text this reply typed into its field doesn't count as a change."""
        after = getattr(self, "_settled", None)
        if not result.typed or after is None:
            return settled == seen.signature()
        typed = after.holding(result.typed)
        return after.signature(skip=typed) == seen.signature(skip=typed)

    async def _pixels_moved(self) -> bool | None:
        """Did the pixels change since the last look? None: can't say."""
        grab = getattr(self.capturer, "glance", None)
        shots = list(getattr(self, "_shots", None) or [])
        shot = next((item for item in shots if item.screen.is_cursor_screen), shots[0] if shots else None)
        if grab is None or shot is None:
            return None
        frame = getattr(getattr(self, "_context", None), "window_frame", None)

        def compare() -> bool:
            import io

            from PIL import Image

            from mcp_vision.buddy.watch import changed, glance

            before = Image.open(io.BytesIO(shot.data))
            now = grab()
            return changed(glance(before, _region(frame, shot, before.size)),
                           glance(now, _region(frame, shot, now.size)))
        try:
            return await asyncio.to_thread(compare)
        except Exception:
            return None

    def _circling(self, context: ScreenContext | None, guide: bool) -> str:
        """A note once a task lands on the same screen a third time."""
        if not (guide and self._goal and context is not None and not context.empty):
            return ""
        key = context.content_signature()
        self._screens_seen[key] = self._screens_seen.get(key, 0) + 1
        if self._screens_seen[key] < 3:
            return ""
        return (f"\nnote: you've now seen this same screen {self._screens_seen[key]} times during this task. if "
                "you're not getting closer, change approach: x,y from the screenshot instead of an id, the keyboard, "
                "another control. only ask them if it's something only they can do (a login, a code, a detail you "
                "don't have).")

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
        await self._drain()
        if outcome.status == "done" and self._goal:
            # the yes was one step of a task: keep going
            done = outcome.result
            result.acted.append(f"{outcome.spec.name}: {done.report or 'done'}" if done else f"{outcome.spec.name}: done")
            if done is not None:
                result.look_after, result.settle = done.look_after, done.settle
            result.hands = outcome.spec.skill == "control"
            result.goal = self._goal
            result.route = self._goal_route or result.route
            return await self._drive(result)
        if outcome.status != "done":
            self._goal = ""
        return result

    # -- timers and other late announcements (called from worker threads) ------------------
    def _read(self) -> list[str]:
        """The frontmost page's text (worker thread). No screenshot."""
        reader = getattr(self.context, "read", None)
        if reader is None:
            return []
        try:
            return list(reader() or [])
        except Exception:
            return []

    def _fingerprint(self, x: float, y: float) -> bytes | None:
        """Tiny gray pixels around a point (worker thread), to tell if a scroll moved."""
        grab = getattr(self.capturer, "fingerprint_at", None)
        if grab is None:
            return None
        try:
            return grab(x, y)
        except Exception:
            return None

    def _images_for(self, shots: list[Screenshot], context: ScreenContext | None, *, guide: bool,
                    lean: bool) -> tuple[list[Screenshot], str]:
        """Screenshots to send, plus a note when skipped (task steps lean on the map; a shot is ~1,400 tokens)."""
        force = bool(self.actions is not None and self.actions.ctx.state.pop("force_image", False))
        if not (self.vision and shots):
            return [], ""
        digest = hashlib.sha1(b"".join(shot.data for shot in shots)).hexdigest()
        previous, self._last_image = getattr(self, "_last_image", None), digest
        if force:
            return shots, ""
        if lean and guide and previous == digest:
            return [], "(screen unchanged since your last look, so no new screenshot.)"
        if lean and context is not None and context.rich:
            self._last_image = previous           # nothing sent: compare with what the model saw
            return [], ("(no screenshot this step to save tokens; the controls and text are current. "
                        "[DO:look {}] shows pixels.)")
        return shots, ""

    def _observe(self):
        """The screen map right now (any thread). No screenshot, no tokens."""
        if self.context is None:
            return None
        try:
            return self.context.snapshot()
        except Exception:
            return None

    def _spent(self, what: str, since: float) -> None:
        """Add perf-clock seconds since ``since`` to the meter's ``what``."""
        meter = self._meter
        if meter is not None:
            setattr(meter, what, getattr(meter, what) + time.perf_counter() - since)

    async def _wait_for_screen(self, result: TurnResult) -> str | None:
        """Let the last step land; the settled map's signature, if watched."""
        if result.look_after:
            if result.look_after >= 1.0:
                self.emit("step", id="wait", label="Waiting for it to load", status="active")
            slept = time.perf_counter()
            await asyncio.sleep(result.look_after)
            self._spent("settle", slept)
        settled = None
        if result.settle:
            settled = await self._settle(result.settle)
            result.settle = None
        return settled

    async def _settle(self, limit: float) -> str | None:
        """Wait up to ``limit`` s for the map to settle: its signature (map in ``_settled``), None if no map."""
        self._settled = None
        began = time.perf_counter()
        try:
            return await self._settle_for(limit)
        finally:
            self._spent("settle", began)

    async def _settle_for(self, limit: float) -> str | None:
        if self.context is None:
            await asyncio.sleep(min(limit, 1.5))
            return None
        started = self.clock()
        steady, quiet, signature = None, 0, None
        while self.clock() - started < limit:
            seen = await asyncio.to_thread(self._observe)
            signature = seen.signature() if seen is not None and not seen.empty else None
            self._settled = seen if signature is not None else None
            if signature is None and self.clock() - started > 1.5:
                return None                           # no map here: a short wait is all we can do
            # stillness ignores self-changing bits (clocks, counters)
            calm = seen.settle_signature() if signature is not None else None
            quiet = quiet + 1 if calm is not None and calm == steady else 0
            steady = calm
            if quiet >= 2:
                return signature
            await asyncio.sleep(self.settle_interval)
        return signature

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
        """Per-turn notes: the time (kept out of the cached prompt)."""
        import datetime as _dt

        return "now: " + _dt.datetime.now().strftime("%A, %B %d %Y, %I:%M %p").replace(" 0", " ")

    async def _turn(self, transcript: str, *, guide: bool = False, screen: bool | None = None,
                    route: Route | None = None, record_as: str | None = None, lean: bool = False,
                    effort: str | None = None) -> TurnResult:
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
        cancelled = called = False
        meter = self._meter                    # this request's, even if a newer press replaces self._meter
        try:
            shots, context, result.route = await self._look(transcript, result, screen, route)
            mark("looked")
            if screen is not False or not getattr(self, "_shots", None):
                # a follow-up that didn't look keeps the last screen to aim with
                self._shots, self._context = shots, context
                if self.actions is not None:                    # fresh look: ids are current again
                    self.actions.ctx.state.pop("scrolled", None)
                    self.actions.ctx.state.pop("stale_map", None)
            history = self.conversation.history()
            images, skipped = self._images_for(shots, context, guide=guide, lean=lean)
            extra, self._extra_note = self._extra_note, ""
            notes = self._notes() + self._circling(context, guide) + (f"\n{skipped}" if skipped else "") + \
                (f"\n{extra}" if extra else "")
            text = user_turn_text(transcript, shots, context, vision=bool(images), notes=notes)
            system = self.system_prompt
            turn = Turn("user", text, images=tuple(images))
            reply = ReplyStream()
            badge = brain_badge(self.brain)
            self.emit("engine", **badge)
            self.emit("step", id="think", label=f"{badge['label']} is thinking", status="active")
            first = True
            called = True
            streaming, busy = time.perf_counter(), (meter.act + meter.settle if meter is not None else 0.0)
            per_call = {"effort": effort} if effort and _takes_effort(self.brain) else {}
            async for delta in self.brain.stream(system=system, turns=[*history, turn],
                                                 detailed=result.route.detailed, **per_call):
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
            called = False
            if meter is not None:                     # the brain's time, not the steps it ran mid-reply
                meter.model += time.perf_counter() - streaming - (meter.act + meter.settle - busy)
            if result.held:
                self.speaker.speak("The rest is on screen.")
            if reply.leaked:
                self._leaked(result)
            reported = getattr(self.brain, "last_usage", None)
            self._meter_turn(meter, reported if isinstance(reported, Usage) else estimate(
                text_tokens(system) + sum(text_tokens(t.text) for t in [*history, turn])
                + sum(image_tokens(shot.width, shot.height) for shot in turn.images),
                reply.spoken_text, str(getattr(self.brain, "model", "") or "")))
            said = (result.muted.get(index, text) for index, text in enumerate(reply.spoken))
            result.spoken = " ".join(text for text in said if text)
            result.finished = reply.done
            result.steps_total = reply.steps
            result.plan = reply.plan or result.plan
            result.goal = reply.goal or result.goal
            if result.pending and "?" not in result.spoken:
                ask = f"Want me to {_spoken(result.pending)}? Say yes and I'll do it."
                self.speaker.speak(ask)
                self.emit("answer", text=(" " if result.spoken else "") + ask)
                result.spoken = (result.spoken + " " + ask).strip()
            self.conversation.record(record_as or transcript,
                                     _history_text(reply, result.targets, result.did)
                                     or ("(wrote a tool call as text; nothing was said)" if reply.leaked else ""),
                                     step=guide)
            self.emit("done", latency_ms=result.timings.get("first_speech"), spoken=result.spoken)
            if not self._continues(result):
                await self._drain()
            # else: more steps follow; keep working while it talks
            mark("spoken")
        except asyncio.CancelledError:
            # A new press owns the overlay now (it is already "listening");
            # don't stomp on it with "idle" from this abandoned turn.
            cancelled = True
            if called:
                self._meter_turn(meter, Usage())        # stopped mid-answer: still a call they made
            self.speaker.stop()
            self.pointer.release()
            raise
        except Exception as exc:  # surfaced to the user, never silently swallowed
            if called:
                self._meter_turn(meter, Usage())        # the brain was asked and failed: a call, no tokens known
            result.state = "error"
            result.error = _friendly_error(exc)
            self.emit("error", message=result.error)
            self.speaker.stop()
            self.speaker.speak(result.error)
            await self._drain()
        finally:
            if not cancelled:
                self.pointer.set_state("idle")
        return result

    async def _look(self, transcript: str, result: TurnResult, screen: bool | None = None,
                    routed: Route | None = None) -> tuple[list[Screenshot], ScreenContext | None, Route]:
        """Route, capture, and read screen context concurrently (``routed``: a follow-up reuses its route)."""
        if screen is False:                     # e.g. handing search results back: no need to look
            return [], None, replace(routed or Route(provider="followup"), needs_screen=False)
        started = self.clock()
        prefetched = self._take_prefetch()
        capture = (asyncio.wrap_future(prefetched) if prefetched is not None
                   else asyncio.create_task(asyncio.to_thread(self.capturer.capture)))
        mapped = self._take_map() if self.context is not None else None
        context_task = mapped if mapped is not None else \
            (asyncio.create_task(asyncio.to_thread(self.context.snapshot)) if self.context is not None else None)
        route = routed or Route()
        if self.router is not None and routed is None:
            try:
                screens = await asyncio.wait_for(asyncio.to_thread(self.capturer.screens), self.capture_timeout)
                route = await self.router.route(transcript, screens)
            except Exception:
                route = Route(provider="fallback")
            self.emit("step", id="route", status="done", detail=f"{route.latency_ms:.0f}ms",
                      label="Needs your screen" if route.needs_screen else "No screen needed")
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
            route = replace(route, needs_screen=True)
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
            self.emit("step", id="map", label=f"Found {min(len(context.controls), 70)} buttons and fields",
                      status="done")
        return shots, context, route

    async def _handle(self, event, shots, result: TurnResult, mark) -> None:
        if isinstance(event, SpeechChunk):
            index, result.heard = result.heard, result.heard + 1
            if result.hushed:
                # mute claims after a failed or pending step; a pending step keeps its questions
                asks = " ".join(re.findall(r"[^.!?]*\?+", event.text)).strip() if result.pending else ""
                result.muted[index] = asks
                if not asks:
                    return
                event = SpeechChunk(asks)
            first = "first_speech" not in result.timings
            if first:
                mark("first_speech")
                if self._meter is not None and self._meter.first is None:
                    self._meter.first = time.time() - self._meter.started
                self.pointer.set_state("speaking")
                self.emit("phase", phase="answering")
            # ~a minute aloud (double for depth); the rest is read on screen
            budget = SPEECH_BUDGET * (2 if result.route.detailed else 1)
            if result.held or (result.said and result.said + len(event.text) > budget):
                result.held = True
            else:
                result.said += len(event.text)
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
        elif isinstance(event, GoalTag):
            if event.text != self._goal:
                self.emit("goal", text=event.text, done=False)
            self._goal = result.goal = event.text
        elif isinstance(event, ActionTag):
            await self._act(event, result)
        elif isinstance(event, PointTag):
            target = resolve_target(event, shots)
            if target is None:
                return
            listed = _listed(target, getattr(self, "_context", None))
            if listed is not None:
                # aimed with the map's numbers: already on the control
                target = replace(target, x=listed.x, y=listed.y, source="snapped")
            elif self.snapper is not None:
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
                      label=f"Pointed at {target.label or 'it'}")


    def _leaked(self, result: TurnResult) -> None:
        """The model wrote a tool call as text (not run, not said): tell it, once a request."""
        self.emit("step", id="leak", label="Skipped a command I can't run", status="skipped")
        self._leaks += 1
        if self._leaks > 1:
            return                                    # told once already
        note = ("note: your reply wrote a tool call or shell command out as text. you have no shell, terminal or file "
                "tools, so it didn't run, and nothing from there on was said. do it with [DO:…] actions instead.")
        result.acted.append(note)
        result.reports.append(note)

    async def _act(self, tag: ActionTag, result: TurnResult) -> None:
        if self.actions is None:
            return
        spec_now = self.actions.specs.get(tag.name)
        on_screen = spec_now is not None and (spec_now.skill == "control" or tag.name in _ON_SCREEN)
        if result.pending or (result.failed and on_screen):
            # written as if the pending/failed step went through: skip it, the next look re-plans
            if result.failed and not any(line.startswith("note: the steps after") for line in result.acted):
                result.acted.append("note: the on-screen steps after the one that failed weren't run")
            self.emit("step", id=f"skipped-{tag.name}-{len(result.did)}", label=tag.name.replace("_", " "),
                      status="skipped", detail="after one that didn't go through")
            return
        self.actions.ctx.schedule = self._schedule
        self.actions.ctx.announce = self.announce
        self.actions.ctx.screen = (getattr(self, "_shots", []), getattr(self, "_context", None))
        self.actions.ctx.observe = self._observe
        self.actions.ctx.read = self._read
        self.actions.ctx.fingerprint = self._fingerprint
        self.actions.ctx.animate = lambda x, y, label: self.pointer.point(x, y, label)
        self._action_seq = getattr(self, "_action_seq", 0) + 1
        step_id = f"action-{self._action_seq}"
        spec = self.actions.specs.get(tag.name)
        key = f"{tag.name} {json.dumps(tag.args, sort_keys=True)}"
        self._trail = [*self._trail[-5:], key]
        repeats = 0
        for previous in reversed(self._trail):
            if previous != key:
                break
            repeats += 1
        if repeats >= 3 and (self._goal or result.hands):
            result.acted.append(f"note: that's {tag.name} with the same arguments {repeats} times in a row. if it "
                                "isn't getting you closer, do something different: aim another way, use the keyboard "
                                "or another control.")
        if tag.name == "wait" and result.settle:
            pass                                      # the settle is the wait
        elif result.settle and spec is not None and (spec.skill == "control" or tag.name in _ON_SCREEN):
            # an earlier action is still loading: wait for it
            settled = await self._settle(result.settle)
            result.settle = None
            seen, after = getattr(self, "_context", None), getattr(self, "_settled", None)
            if settled is not None and seen is not None and after is not None \
                    and after.signature(values=False, digits=False) != seen.signature(values=False, digits=False):
                self.actions.ctx.state["stale_map"] = True    # the ids the model saw are gone
        label = spec.describe(tag.args) if spec else tag.name.replace("_", " ")
        self.emit("step", id=step_id, label=label, status="active")
        consent = None if self._authored else self._consent
        acting = time.perf_counter()
        outcome = await self.actions.handle(tag.name, tag.args, consent=consent)
        self._spent("act", acting)
        if tag.name in _AUTHORING and outcome.status == "done":
            self._authored = True
        if outcome.status == "pending":
            preview = outcome.preview
            result.pending = preview.title
            result.hushed = True                      # no "Bought." until they say yes
            self.emit("step", id=step_id, label=label, status="done", detail="waiting for your OK")
            self.emit("confirm", title=preview.title, lines=preview.lines, confirm=preview.confirm, name=tag.name)
            return
        if spec is not None and spec.skill == "control" or (tag.name == "type_text" and "id" in tag.args):
            result.hands = True
        if outcome.status == "done":
            action_result = outcome.result
            result.did.append(f"{label} ({action_result.note})" if action_result.note else label)
            if self._meter is not None:
                self._meter.actions.append(tag.name)    # names only
            result.acted.append(f"{tag.name}: " + (action_result.report or action_result.note
                                                   or f"done ({label.lower()})"))
            if action_result.report:
                result.reports.append(f"{tag.name}: {action_result.report}")
            if action_result.look_after:
                result.look_after = max(result.look_after or 0.0, action_result.look_after)
            if action_result.settle:
                result.settle = max(result.settle or 0.0, action_result.settle)
            result.opened = label if action_result.opens else ""
            if tag.name in _AUTHORING:
                result.typed.append(str(tag.args.get("text") or ""))
            detail = "you said yes" if outcome.agreed else action_result.detail
            self.emit("step", id=step_id, label=label, status="done", detail=detail)
            self.emit("action", name=tag.name, status="done", label=label, detail=action_result.detail,
                      items=action_result.items)
            if action_result.say:
                self.speaker.speak(action_result.say)
                self.emit("answer", text=" " + action_result.say)
            return
        # failed, unknown, disabled: the model hears why next turn
        result.failed.append(label)
        hint = f" ({outcome.hint})" if outcome.hint else ""
        result.acted.append(f"{tag.name} failed: {outcome.message}{hint}")
        result.hushed = True                          # the rest assumed it worked
        self.emit("step", id=step_id, label=label, status="failed", detail=outcome.message)
        self.emit("action", name=tag.name, status="failed", label=label, detail=outcome.message)
        if self._goal or result.hands or result.route.multistep:
            return                  # the next step explains it: don't say it twice
        self.speaker.speak(outcome.message)
        self.emit("answer", text=" " + outcome.message)


# the whole utterance is "keep going" (plus an ok or please)
CONTINUE_RE = re.compile(r"^\W*((ok(ay)?|yes|yeah|sure|alright)\W+)?((you can|please)\s+)?(keep going|continue|go on|"
                         r"carry on|keep at it|resume|don't stop|finish (it|up|the job)|go ahead and finish)"
                         r"(\W+(please|then|now|plip))?\W*$", re.IGNORECASE)
# The whole utterance closes the session: "thanks", "ok bye", "that's all for now", "never mind".
FAREWELL_RE = re.compile(
    r"^\W*((ok(ay)?|alright|cool|great|perfect|awesome|nice|got it|no)\W+)*"
    r"(thanks?( you)?( so much| a lot)?|thank you( so much| very much)?|ty|cheers|bye( bye)?|goodbye|good ?night|"
    r"see (you|ya)( later)?|later|that'?s (all|it|everything)( for now)?|that'?ll be all|we'?re (done|good)|"
    r"all done|i'?m (done|good|all set)|nothing else|never ?mind)"
    r"(\W+(plip|bye|thanks?|thank you|that'?s all|for now|buddy|man))*\W*$", re.IGNORECASE)
SPEECH_BUDGET = 900          # chars read aloud per reply (~a minute)
# these act on the current screen, so they wait for loads first
_ON_SCREEN = {"type_text", "replace_selection", "read_page"}
_AUTHORING = {"type_text", "replace_selection"}         # Plip's text in a field isn't the page moving
# results the next step must read and judge
_READING = {"read_page", "search_files", "look", "find_flights", "list_shortcuts", "run_shortcut", "web_search"}
# result lines meaning a step got nowhere
_STUCK = (" failed: ", "nothing on screen changed", "only the address", "you ended with [DONE]")


def _region(frame: Rect | None, shot: Screenshot, size: tuple[int, int]) -> tuple[float, float, float, float]:
    """The focused window's box in the image; else the screen minus the menu bar (its clock ticks)."""
    width, height = size
    screen = shot.screen.frame
    if frame is None or screen.width <= 0 or screen.height <= 0:
        return 0, height * 0.04, width, height
    sx, sy = width / screen.width, height / screen.height
    left, top = max(frame.x - screen.x, 0) * sx, max(frame.y - screen.y, 0) * sy
    right = min(frame.x + frame.width - screen.x, screen.width) * sx
    bottom = min(frame.y + frame.height - screen.y, screen.height) * sy
    return left, max(top, height * 0.04), right, bottom


def _takes_effort(brain: Any) -> bool:
    """Does this brain's stream() take a per-call effort?"""
    import inspect

    try:
        return "effort" in inspect.signature(brain.stream).parameters
    except (TypeError, ValueError):
        return False


def reply_asks(spoken: str) -> bool:
    """Does the reply end by asking the user something?"""
    return "?" in spoken.strip()[-160:]


def _spoken(title: str) -> str:
    """A confirm card's title as words to say ("Press cmd+q" -> "press command Q")."""
    words = title.replace("“", "").replace("”", "").strip()
    keys = {"cmd": "command", "alt": "option", "opt": "option", "ctrl": "control", "esc": "escape"}
    words = re.sub(r"\b(cmd|alt|opt|ctrl|esc)\b", lambda m: keys[m.group(1)], words)
    words = re.sub(r"\+(\w)\b", lambda m: " " + m.group(1).upper(), words).replace("+", " ")
    return words[:1].lower() + words[1:]


def _question(spoken: str) -> str:
    """The question a reply ends on, plus the sentence before (what "it" is); "" if it doesn't ask."""
    if not reply_asks(spoken):
        return ""
    sentences = [part.strip() for part in re.findall(r"[^.!?]+[.!?]*", spoken.strip()) if part.strip()]
    asking = [index for index, sentence in enumerate(sentences) if "?" in sentence]
    if not asking:
        return ""                                     # "?" on its own: nothing it asked about
    last = asking[-1]
    return " ".join(sentences[max(0, last - 1):last + 1])


# a question a yes or no answers ("want me to…", "should I…")
_YES_NO_RE = re.compile(r"^(want|wanna|should|shall|do|does|did|can|could|would|will|is|are|was|were|have|has|may)\b",
                        re.IGNORECASE)


def _farewell(transcript: str) -> str:
    said = transcript.lower()
    if re.search(r"\b(bye|good ?night|see (you|ya)|later)\b", said):
        return "See you."
    if re.search(r"\b(thanks?|thank you|ty|cheers)\b", said):
        return "Anytime."
    return "Okay. I'm here when you need me."


def _offer(asked: str) -> str:
    """The yes/no question a reply ends on; "" for an open question or a choice (a Yes button can't answer)."""
    questions = [part.strip() for part in re.findall(r"[^.!?]+[.!?]*", asked or "") if "?" in part]
    if not questions:
        return ""
    question = questions[-1]
    if re.search(r"\bor\b", question, re.IGNORECASE) or not _YES_NO_RE.match(question):
        return ""
    return question


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _outcome(result: TurnResult) -> str:
    """How a request ended, for the Usage tab: did the job get done?"""
    if result.state == "cancelled":
        return "stopped"
    if result.state == "error":
        return "failed"
    if result.outcome:
        return result.outcome                         # set on the way (paused)
    if result.pending:
        return "waiting"                              # an action is waiting for their yes
    if result.goal or result.hands:
        if result.finished:
            return "unverified" if result.verified is False else "done"
        return "waiting"                              # it stopped to ask them something
    if result.reports:
        return "unverified"                           # it acted, but ran out of turns to check the results
    if result.steps_total and not result.finished:
        return "paused"                               # a walkthrough that didn't reach the last step
    if result.failed and not result.did:
        return "failed"
    return "done" if result.did or result.steps_total else "answered"


def _listed(target: Target, context: ScreenContext | None, tolerance: float = 3.0):
    """The map control centered on this point (within whole-pixel rounding), if any."""
    if context is None:
        return None
    return next((control for control in context.ids.values()
                 if abs(control.x - target.x) <= tolerance and abs(control.y - target.y) <= tolerance), None)


def _history_text(reply: ReplyStream, targets: list[Target], did: list[str] | None = None) -> str:
    """What the assistant 'said' last turn, including where it pointed and what it did."""
    text = reply.spoken_text
    if reply.goal:
        text = f"[GOAL: {reply.goal}] " + text
    if reply.plan:                                    # the checklist they see: later steps follow it
        text = f"[PLAN: {' | '.join(reply.plan)}] " + text
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
    if "no longer supported for gemini code assist" in lowered or "ineligibletier" in lowered:
        return ("Google stopped letting the Gemini CLI answer on free accounts. Use the free Gemini key in my settings "
                "instead.")
    if "tried to run a command" in lowered:
        return "My brain tried to run a command on your Mac, and I don't let it do that. Try asking again."
    if "not logged in" in lowered or "login" in lowered or "sign in" in lowered:
        return "I need you to sign in to my brain first. Open my settings and pick a brain."
    if "not installed" in lowered or "no such file" in lowered:
        return "My brain app isn't installed. Open my settings and pick another one."
    if "api key" in lowered or "401" in lowered or "authentication" in lowered:
        return "I can't reach my model. Check the API key in your settings."
    if "waiting for network" in lowered or "connection failed" in lowered or "network" in lowered:
        return "I can't reach my brain right now. Check your internet connection and try again."
    if ("free_tier" in lowered or "free tier" in lowered) and ("perday" in lowered or "per day" in lowered):
        return "Google's free AI limit for today is used up. It resets tomorrow, or connect another AI in my settings."
    if "free_tier" in lowered or "free tier" in lowered:
        return "Google's free AI takes a few questions a minute, and that's used up. Give it a minute and try again."
    if "429" in lowered or "rate" in lowered or "usage limit" in lowered or "quota" in lowered:
        return "I'm being rate limited right now. Give me a moment and try again."
    if "timeout" in lowered or "timed out" in lowered:
        return "That took too long. Try asking again."
    return "Something went wrong on my end. Try asking again."


__all__ = ["SYSTEM_PROMPT", "Brain", "Companion", "Route", "Target", "TurnResult", "resolve_target", "brain_badge"]
