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
    multistep: bool = False          # doing it takes several actions ("open X and do Y", "find me jobs here")
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
    outcome: str = ""                # how the request ended (usage.OUTCOMES), set when it's over
    usage: Usage | None = None       # tokens the whole request used (as the brain reported, or estimated)
    goal: str = ""                   # the multi-step task this request worked toward ([GOAL: ...])
    acted: list[str] = field(default_factory=list)        # what this model turn's actions did (incl. failures)
    settle: float | None = None      # an action changed the screen: wait up to this long for it to settle
    hands: bool = False              # Plip clicked, scrolled or pressed keys on screen this request
    opened: str = ""                 # this turn's last action opened something (a link, a page): what it was
    verified: bool | None = None     # [DONE] after acting: True = the screen confirmed it, False = couldn't tell
    hushed: bool = False             # a step failed: the rest of the reply was written as if it worked
    said: int = 0                    # characters spoken this model turn (the speech budget)
    held: bool = False               # stopped reading aloud; the rest of the answer is on screen
    typed: list[str] = field(default_factory=list)        # text this reply put in fields: it showing isn't a change
    muted: dict[int, str] = field(default_factory=dict)  # ...so those chunks (by position) aren't said, or only this
    heard: int = 0                   # chunks the reply has streamed so far


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
        self.max_agent_steps = max_agent_steps        # click/scroll/type loops and goals get a bigger budget
        self.step_effort = step_effort                # routine task steps think this hard (None: the user's depth)
        self._goal = ""                               # the open multi-step task, until [DONE]
        self._goal_waiting = False                    # the task stopped to ask the user something
        self._rejected = 0                            # [DONE]s sent back this task because the last step didn't land
        self._extra_note = ""                         # one-turn context for the model ("you were working toward…")
        self._trail: list[str] = []                   # the last few actions, to notice the same one on repeat
        self._hiccups = 0                             # steps that failed or went nowhere this task
        self._screens_seen: dict[str, int] = {}       # screens visited this task, to notice going in circles
        self._leaks = 0                               # tool calls it wrote out as text this request
        self._asked = ""                              # the question Plip's last reply ended on
        self._consent = None                          # a yes they gave this request: the step it names won't ask
        self._authored = False                        # Plip typed something this request (they haven't seen it yet)
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
        """Plip's instructions, then what it knows about the user.

        What it knows changes only when they tell it something, so it rides in the cached prompt rather than in
        each turn, where every call (each walkthrough step and action result too) paid for it again in full.
        """
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
        if self.context is not None:                  # the screen map too: a browser page can take ~0.3 s to walk
            self._mapped = (self.clock(), self._pool.submit(self._observe))

    def warm_brain(self, effort: str | None = None) -> None:
        """Have a brain process ready before the question exists (key press, app start). On the loop."""
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
        meter = self._meter = _Meter(started=time.time())
        self._task = asyncio.ensure_future(coroutine)
        try:
            result = await self._task
        except asyncio.CancelledError:
            result = TurnResult(transcript=transcript, state="cancelled")
        result.usage = meter.usage if meter.turns else None
        result.outcome = _outcome(result)
        result.timings.update(meter.timings())
        self._asked = _question(result.spoken) if result.state == "done" and not result.pending else ""
        self._consent, self._authored = None, False   # a yes lasts one request
        self._record(meter, result)
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
        # "Yes" to Plip's "want me to send it?" was the confirmation: the step it named doesn't ask again.
        self._consent = Consent.given(transcript, asked)
        waiting, self._goal_waiting = self._goal_waiting, False
        if self._goal and CONTINUE_RE.match(transcript):
            return await self._resume(transcript)       # the same steps, picked back up: a retype is still a repeat
        if self.actions is not None:
            # They said something (an answer, or something new): typing what came before again isn't a repeat.
            self.actions.ctx.state.pop("typed", None)
        if waiting and self._goal:
            # The task stopped to ask them something; this is most likely the answer, so the task goes on.
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
            # The model forgot [GOAL] on something that clearly takes several steps: hold it to the request.
            self._goal = result.goal = _clip(transcript, 160)
            self.emit("goal", text=self._goal, done=False)
        return await self._drive(result)

    async def _resume(self, transcript: str) -> TurnResult:
        """"Keep going": pick an unfinished goal back up with a fresh look and a fresh step budget."""
        self.emit("phase", phase="thinking", transcript=transcript, guide=False)
        self.emit("goal", text=self._goal, done=False)
        result = TurnResult(transcript=transcript, acted=[f"the user said: {transcript}"], goal=self._goal)
        self._screens_seen = {}                       # a fresh go: old visits don't count toward "going in circles"
        return await self._drive(result)

    def _continues(self, result: TurnResult) -> bool:
        """Does this reply need another model turn on its own (no user input)?"""
        if result.state != "done" or result.pending:
            return False
        if result.finished and (result.hands or self._goal):
            return False
        if result.reports:
            return True                              # results it hasn't read yet (search hits, a shortcut's output)
        if reply_asks(result.spoken):
            return False                             # it asked them something: wait for the answer, don't push on
        if result.look_after:
            return True
        return bool(self._goal and result.acted)     # working toward a goal and just did something: look again

    async def _drive(self, result: TurnResult, turns: int = 1) -> TurnResult:
        """Hand results back, take fresh looks, and keep working toward an open goal until [DONE]."""
        followups = 0
        goal = self._goal
        route = result.route                   # Plip's own follow-ups aren't routed again: same depth, same screens
        start = getattr(self, "_context", None)
        if self._goal and start is not None and not start.empty:     # the screen the task started from counts too
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
                self.warm_brain(self._step_effort(result, result.acted))   # starts up while the screen settles
            settled = await self._wait_for_screen(result)
            agent = bool(self._goal) or result.hands
            lines = list((result.acted if agent else result.reports) or ["(nothing else)"])
            after = getattr(self, "_settled", None)
            pixels, unchanged = None, None
            if agent and changes and settled is not None and seen is not None:
                unchanged = self._unchanged(settled, seen, result)
                # Typed text always moves the pixels, so they only get a say when nothing was typed.
                if unchanged and not result.typed and (pixels := await self._pixels_moved()):
                    # The map can't see what changed (a web page it isn't reading, a canvas): the pixels can.
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
                # More steps only for a task that's plainly getting somewhere: the screen moved on, nothing
                # failed or went nowhere (now or more than once before), no [DONE] was sent back, no circling.
                circling = max(self._screens_seen.values(), default=0) >= 3
                if moved and limit < 2 * self.max_agent_steps and not self._rejected and not circling and \
                        self._hiccups <= 1 and not any(sign in line for line in lines for sign in _STUCK):
                    limit += 5                        # still getting somewhere: a few more steps, not a pause
                else:
                    lines.append("note: this is your last step before plip pauses for them. finish the goal if this "
                                 "step does it; otherwise say in a few words where you got to and that you'll carry "
                                 "on when they say keep going. never hand them the remaining steps to do.")
            if any(sign in line for line in lines for sign in _STUCK):
                self._hiccups += 1
            reports = "\n".join(f"- {line}" for line in lines)
            template = AGENT_FOLLOWUP if agent else LOOK_FOLLOWUP if result.look_after else ACTION_FOLLOWUP
            toward = f" toward: {self._goal}" if self._goal else ""
            prompt = template.format(reports=reports, step=followups + 1, toward=toward)
            if self._goal:
                self.emit("goal", text=self._goal, done=False, step=followups + 1)
            # Recorded short: the next step needs the gist, not every report again.
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
            self._goal_waiting = True                 # it asked them something: their answer carries the task on
        elif self._goal and self._continues(result):
            # Out of steps but not done: say so instead of going quiet, and keep the goal for "keep going".
            pause = "I'll check in with you here. Say keep going and I'll pick it back up."
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
        await self.speaker.drain()                  # the steps ran while it talked; now let it finish
        return result

    def _step_effort(self, result: TurnResult, lines: list[str], moved: bool | None = None) -> str | None:
        """How hard a task step thinks. A routine one (the last step worked and the screen moved on) gets
        ``step_effort``: thinking there cost ~1.5 s a step for the same next click. Reading results to judge
        them, and anything that went wrong (a failure, a rejected [DONE], no change, going in circles), keeps
        the user's depth. None means the user's depth."""
        if not self.step_effort or moved is False:
            return None
        if any(report.split(":", 1)[0] in _READING for report in result.reports):
            return None                               # page text, search hits, a closer look: judge them properly
        if self._rejected or self._leaks or any(" failed: " in line or line.startswith("note:") for line in lines):
            return None
        if max(self._screens_seen.values(), default=0) >= 2:
            return None                               # back on a screen it's seen: think it through
        return self.step_effort

    async def _check_done(self, result: TurnResult) -> str:
        """[DONE] in the same reply as an action: did that action actually land?

        Model-free and cheap: a step that failed, or a screen that didn't change after a step
        that should have changed it, means the goal isn't met. Twice per task at most, so a
        stubborn page can't loop forever. Returns why it isn't done, or "" to accept.
        """
        if not result.acted:
            return ""                                 # it looked at the results first, then said done: trust it
        if self._rejected >= 2:
            result.verified = False
            return ""
        failed = [line for line in result.acted if " failed: " in line]
        problem = ""
        if failed:
            problem = (f"note: you ended with [DONE], but {failed[-1]}. the goal isn't met yet: fix it another way "
                       "(another way to aim, the keyboard, another control); only tell them what's in the way if it's "
                       "something only they can sort out.")
        elif result.opened and self._rejected == 0:
            # Opening a playlist isn't playing it, and opening a product isn't adding it: one look first.
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
        """The screen after a step is the one the model saw. What this reply typed, sitting in the field it went
        into, doesn't count: typing then a return or a click that did nothing would pass otherwise. Any other
        field's value does (a search cleared, the city filled in from a zip code)."""
        after = getattr(self, "_settled", None)
        if not result.typed or after is None:
            return settled == seen.signature()
        typed = after.holding(result.typed)
        return after.signature(skip=typed) == seen.signature(skip=typed)

    async def _pixels_moved(self) -> bool | None:
        """Did the screen itself change since the last look? For when the map can't tell. None: can't say."""
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
        """Back on the same screen a third time in one task: say so before the budget burns down."""
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
        await self.speaker.drain()
        return result

    # -- timers and other late announcements (called from worker threads) ------------------
    def _read(self) -> list[str]:
        """The whole frontmost page's text (worker thread). No screenshot, no tokens until it's reported."""
        reader = getattr(self.context, "read", None)
        if reader is None:
            return []
        try:
            return list(reader() or [])
        except Exception:
            return []

    def _fingerprint(self, x: float, y: float) -> bytes | None:
        """The pixels around a point, tiny and gray (worker thread): did a scroll move anything there?"""
        grab = getattr(self.capturer, "fingerprint_at", None)
        if grab is None:
            return None
        try:
            return grab(x, y)
        except Exception:
            return None

    def _images_for(self, shots: list[Screenshot], context: ScreenContext | None, *, guide: bool,
                    lean: bool) -> tuple[list[Screenshot], str]:
        """Which screenshots to actually send, and a note for the model when Plip skips them.

        The numbered map already tells the model where everything is, so on a task's steps a screenshot
        only goes out when the map is thin, the model asked to look, or the screen changed in a way the
        map can't show. A screenshot is ~1,400 tokens; most steps of a task don't need one.
        """
        if not (self.vision and shots):
            return [], ""
        force = bool(self.actions is not None and self.actions.ctx.state.pop("force_image", False))
        digest = hashlib.sha1(b"".join(shot.data for shot in shots)).hexdigest()
        previous, self._last_image = getattr(self, "_last_image", None), digest
        if force:
            return shots, ""
        if lean and guide and previous == digest:
            return [], "(screen unchanged since your last look, so no new screenshot.)"
        if lean and context is not None and context.rich:
            self._last_image = previous           # nothing was sent; keep comparing with what the model saw
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
        """Add the seconds since ``since`` (perf clock) to this request's ``what`` (model, act, settle)."""
        meter = self._meter
        if meter is not None:
            setattr(meter, what, getattr(meter, what) + time.perf_counter() - since)

    async def _wait_for_screen(self, result: TurnResult) -> str | None:
        """Let the last step land; returns the settled screen map's signature when Plip watched it settle."""
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
        """Wait until the screen map stops changing (an app opened, a page loaded), at most ``limit`` seconds.

        Re-reads the Accessibility map locally: no screenshot, no tokens. Returns the map's signature when it
        stopped changing (or at the limit), ``None`` without a map to read. ``self._settled`` keeps the map
        itself, so the next step can tell "nothing changed" from "only the title did".
        """
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
                return None                           # no Accessibility map here: a short wait is all we can do
            # Still-ness ignores what changes on its own (a clock, a counter); the answer is the real signature.
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
        """Per-turn notes: the time (it changes every minute, so it stays out of the cached prompt)."""
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
                # A follow-up that didn't look keeps the last screen: actions still aim with it.
                self._shots, self._context = shots, context
                if self.actions is not None:                    # a fresh look: the numbers are current again
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
                ask = f"{result.pending}. Say yes and I'll do it."
                self.speaker.speak(ask)
                self.emit("answer", text=(" " if result.spoken else "") + ask)
                result.spoken = (result.spoken + " " + ask).strip()
            self.conversation.record(record_as or transcript, _history_text(reply, result.targets, result.did),
                                     step=guide)
            self.emit("done", latency_ms=result.timings.get("first_speech"), spoken=result.spoken)
            if not self._continues(result):
                await self.speaker.drain()
            # else: more steps follow, so keep working while it talks ("opening it now" plays on)
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
            await self.speaker.drain()
        finally:
            if not cancelled:
                self.pointer.set_state("idle")
        return result

    async def _look(self, transcript: str, result: TurnResult, screen: bool | None = None,
                    routed: Route | None = None) -> tuple[list[Screenshot], ScreenContext | None, Route]:
        """Route, capture, and read screen context concurrently.

        ``routed``: the request's route, for Plip's own follow-ups. They keep its effort and screens rather than
        routing Plip's words as if the user said them (a router call each, and an effort that changed mid-task).
        """
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
            self.emit("step", id="map", label=f"Mapped {min(len(context.controls), 70)} controls", status="done")
        return shots, context, route

    async def _handle(self, event, shots, result: TurnResult, mark) -> None:
        if isinstance(event, SpeechChunk):
            index, result.heard = result.heard, result.heard + 1
            if result.hushed:
                # "Added it" after the click failed isn't said; after a step waiting for their yes, a question
                # still is ("want me to check out too?"), the claim before it ("Bought!") isn't.
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
            # About a minute of talking, more when they asked for depth. Past that it reads on screen:
            # a reply that long is something to read, not to listen to.
            budget = SPEECH_BUDGET * (2 if result.route.detailed else 1)
            if result.said and result.said + len(event.text) > budget:
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
                # It aimed with the screen map's own numbers: already on the control, nothing to snap.
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
                      label=f"Pointed at {target.label or 'it'}", detail="snapped" if target.source == "snapped" else "")


    def _leaked(self, result: TurnResult) -> None:
        """The model wrote a tool call out as text: it didn't run and wasn't read out. Tell it, once a request."""
        self.emit("step", id="leak", label="Skipped a command I can't run", status="skipped")
        self._leaks += 1
        if self._leaks > 1:
            return                                    # told once already: don't go round in circles
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
            # A step waiting for their yes, or a screen step after one that failed: it was written as if that went
            # through (typing the reply after a Send card, return after a failed click). The next look re-plans.
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
            pass                                      # the wait is the settle: one early-exit wait, not two
        elif result.settle and spec is not None and (spec.skill == "control" or tag.name in _ON_SCREEN):
            # An earlier action in this reply is still loading; this one works on what it shows.
            settled = await self._settle(result.settle)
            result.settle = None
            seen, after = getattr(self, "_context", None), getattr(self, "_settled", None)
            if settled is not None and seen is not None and after is not None \
                    and after.signature(values=False) != seen.signature(values=False):
                self.actions.ctx.state["stale_map"] = True    # the numbers the model saw are gone (typing moves none)
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
            result.hushed = True                      # "Bought." after it: not until they say yes
            self.emit("step", id=step_id, label=label, status="done", detail="waiting for your OK")
            self.emit("confirm", title=preview.title, lines=preview.lines, confirm=preview.confirm, name=tag.name)
            return
        if spec is not None and spec.skill == "control":
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
        # failed, unknown, disabled: the model hears why (and what to try instead) on its next turn
        result.failed.append(label)
        hint = f" ({outcome.hint})" if outcome.hint else ""
        result.acted.append(f"{tag.name} failed: {outcome.message}{hint}")
        result.hushed = True                          # what it wrote after this assumed it worked
        self.emit("step", id=step_id, label=label, status="failed", detail=outcome.message)
        self.emit("action", name=tag.name, status="failed", label=label, detail=outcome.message)
        if self._goal:
            return                  # the next step hears about it and explains in its own words: no saying it twice
        self.speaker.speak(outcome.message)
        self.emit("answer", text=" " + outcome.message)


# The whole utterance is "keep going" (give or take an ok or a please), not "go on linkedin and…".
CONTINUE_RE = re.compile(r"^\W*((ok(ay)?|yes|yeah|sure|alright)\W+)?((you can|please)\s+)?(keep going|continue|go on|"
                         r"carry on|keep at it|resume|don't stop|finish (it|up|the job)|go ahead and finish)"
                         r"(\W+(please|then|now|plip))?\W*$", re.IGNORECASE)
SPEECH_BUDGET = 900          # characters read aloud per reply (about a minute); the rest stays on screen
# Actions that work on whatever is on screen right now, so they wait for an earlier one to finish loading.
_ON_SCREEN = {"type_text", "replace_selection", "read_page"}
_AUTHORING = {"type_text", "replace_selection"}         # Plip's words in a field: them showing isn't the page moving
# Actions whose results the next step reads and judges, rather than just moving on from.
_READING = {"read_page", "search_files", "look", "find_flights", "list_shortcuts", "run_shortcut", "web_search"}
# Lines in a step's results that mean it didn't get anywhere.
_STUCK = (" failed: ", "nothing on screen changed", "only the window title", "you ended with [DONE]")


def _region(frame: Rect | None, shot: Screenshot, size: tuple[int, int]) -> tuple[float, float, float, float]:
    """The focused window in an image of ``shot``'s screen (``size`` pixels); the screen minus its menu bar
    when the window isn't known, so the clock ticking over isn't a change."""
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
    """Does this brain take a per-call effort (the CLIs and the API do; fakes and old brains may not)?"""
    import inspect

    try:
        return "effort" in inspect.signature(brain.stream).parameters
    except (TypeError, ValueError):
        return False


def reply_asks(spoken: str) -> bool:
    """Does the reply end by asking the user something?"""
    return "?" in spoken.strip()[-160:]


def _question(spoken: str) -> str:
    """The question a reply ends on, or "" when it doesn't ask.

    "Want me to send it?" on its own; "I'm about to send it. Can you confirm?" with the sentence
    before, which says what "it" is.
    """
    if not reply_asks(spoken):
        return ""
    sentences = [part.strip() for part in re.findall(r"[^.!?]+[.!?]*", spoken.strip()) if part.strip()]
    last = max(index for index, sentence in enumerate(sentences) if "?" in sentence)
    from mcp_vision.buddy.actions.engine import doings

    start = last if doings(sentences[last]) or last == 0 else last - 1
    return " ".join(sentences[start:last + 1])


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
    """The screen map control whose center this point is (it was written as whole pixels), if any."""
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
