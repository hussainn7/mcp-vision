"""Parse the model's pointing tags out of a streamed reply.

The model answers in plain spoken prose and may embed tags such as::

    [POINT:640,412:Export button]
    [POINT:220,96:File menu:screen2]
    [POINT:none]

Coordinates are pixels in the screenshot of the named screen (the cursor
screen when omitted). It can also act and plan::

    [DO:open_app {"name": "Safari"}]
    [PLAN: open settings | pick privacy | turn on two-factor]
    [GOAL: find remote backend jobs on linkedin]

``[GOAL:...]`` opens a task that takes several actions; Plip keeps looking and
acting until ``[DONE]``.

Action arguments are JSON and may contain brackets, so ``[DO:`` tags are
scanned with a JSON-aware matcher. Tags never reach the speech engine. Each tag is
attached to the sentence it appears in and released just before that
sentence, so the buddy starts flying as the sentence that mentions the
control is spoken - without chopping the sentence in two.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

TAG_RE = re.compile(
    r"\[POINT:\s*(?:"
    r"(?P<none>none)"
    r"|(?P<x>-?\d+(?:\.\d+)?)\s*,\s*(?P<y>-?\d+(?:\.\d+)?)"
    r"(?:\s*:\s*(?P<label>[^\]]*?))?"
    r")\s*\]",
    re.IGNORECASE,
)
_MALFORMED_TAG_RE = re.compile(r"\[\s*POINT\b[^\]]*\]", re.IGNORECASE)
# Walkthrough control tags: "[STEPS:3]" opens a guided task, "[DONE]" ends it.
CONTROL_RE = re.compile(r"\[\s*(?:STEPS\s*:\s*(?P<steps>\d{1,2})|(?P<done>DONE))\s*\]", re.IGNORECASE)
_SCREEN_SUFFIX_RE = re.compile(r"^(?P<label>.*?)(?:\s*:\s*screen\s*(?P<screen>\d+))?\s*$", re.IGNORECASE | re.S)
_SENTENCE_END_RE = re.compile(r"[.!?…]+[\"'”’)\]]*\s+")
_MAX_TAG_LEN = 200
_MAX_ACTION_LEN = 6000               # fill_form tags carry every field
_ACTION_HEAD_RE = re.compile(r"\[\s*DO\s*:\s*(?P<name>[a-z][a-z_]{1,40})\s*", re.IGNORECASE)
_PLAN_RE = re.compile(r"\[\s*PLAN\s*:(?P<steps>[^\[\]]*)\]", re.IGNORECASE)
_GOAL_RE = re.compile(r"\[\s*GOAL\s*:(?P<goal>[^\[\]]*)\]", re.IGNORECASE)
# A tool call written out as text ("<invoke name=…>", "<function_calls>"), with or without a namespace: the brain
# has no tools here, so it never ran, and it's never read out.
_LEAK_RE = re.compile(r"<\s*(?:[\w-]+:)?(?:function_calls\s*>|invoke\s+name\s*=|parameter\s+name\s*=|"
                      r"tool_(?:use|call|code)\b)", re.IGNORECASE)
_THINKING_RE = re.compile(r"<\s*thinking\s*>", re.IGNORECASE)
_THINKING_END_RE = re.compile(r"<\s*/\s*thinking\s*>", re.IGNORECASE)


@dataclass(frozen=True)
class PointTag:
    x: float
    y: float
    label: str = ""
    screen: int | None = None        # 1-based; None means the cursor screen


@dataclass(frozen=True)
class SpeechChunk:
    text: str


@dataclass(frozen=True)
class StepsTag:
    total: int


@dataclass(frozen=True)
class DoneTag:
    pass


@dataclass(frozen=True)
class ActionTag:
    name: str
    args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PlanTag:
    steps: tuple[str, ...]


@dataclass(frozen=True)
class GoalTag:
    text: str


Event = SpeechChunk | PointTag | StepsTag | DoneTag | ActionTag | PlanTag | GoalTag


def _balanced_end(raw: str) -> int | None:
    """Index just past the bracket that closes ``raw[0]``, counting nested brackets."""
    depth = 0
    for position, char in enumerate(raw):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return position + 1
    return None


def scan_special(raw: str) -> tuple[int, ActionTag | PlanTag | GoalTag | None] | None:
    """Match a ``[DO:...]``, ``[PLAN:...]`` or ``[GOAL:...]`` tag at the start of ``raw``.

    Returns ``(length consumed, tag)``; the tag is ``None`` when malformed (it is
    dropped, never spoken). Returns ``None`` when more text is needed.
    """
    head = raw[:12].upper().replace(" ", "")              # "[ GOAL : …" too
    if head.startswith("[PLAN:"):
        match = _PLAN_RE.match(raw)
        if match is None:
            close = raw.find("]")
            if close == -1:
                return None
            return close + 1, None
        steps = tuple(step.strip(" .") for step in match.group("steps").split("|") if step.strip(" ."))[:10]
        return match.end(), (PlanTag(steps) if steps else None)
    if head.startswith("[GOAL:"):
        match = _GOAL_RE.match(raw)
        if match is None:
            end = _balanced_end(raw)
            return (end, None) if end is not None else None
        goal = " ".join(match.group("goal").split()).strip(" .")[:200]
        return match.end(), (GoalTag(goal) if goal else None)
    match = _ACTION_HEAD_RE.match(raw)
    if match is None:
        if len(raw) < 12 and "]" not in raw:
            return None
        close = raw.find("]")
        return (close + 1 if close != -1 else len(raw)), None
    index = match.end()
    if index >= len(raw):
        return None
    name = match.group("name").lower()
    if raw[index] == "]":
        return index + 1, ActionTag(name)
    if raw[index] != "{":
        end = _balanced_end(raw)
        return (end, None) if end is not None else None
    depth, in_string, escape = 0, False, False
    for position in range(index, len(raw)):
        char = raw[position]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = position + 1
                rest = raw[end:]
                stripped = len(rest) - len(rest.lstrip())
                if end + stripped >= len(raw):
                    return None
                if raw[end + stripped] != "]":
                    close = raw.find("]", end)
                    return (close + 1, None) if close != -1 else None
                try:
                    args = json.loads(raw[index:end])
                except ValueError:
                    return end + stripped + 1, None
                return end + stripped + 1, (ActionTag(name, args) if isinstance(args, dict) else None)
    return None


def _next_special(raw: str) -> int:
    """Where the next tag ("[") or markup ("<") might start, or -1."""
    found = [index for index in (raw.find("["), raw.find("<")) if index != -1]
    return min(found) if found else -1


def parse_tag(raw: str) -> PointTag | None:
    """Parse a single complete tag. ``[POINT:none]`` yields ``None``."""
    match = TAG_RE.fullmatch(raw.strip())
    if not match or match.group("none"):
        return None
    label, screen = "", None
    if match.group("label") is not None:
        suffix = _SCREEN_SUFFIX_RE.match(match.group("label"))
        label = (suffix.group("label") if suffix else match.group("label")).strip()
        if suffix and suffix.group("screen"):
            screen = int(suffix.group("screen"))
    return PointTag(x=float(match.group("x")), y=float(match.group("y")), label=label, screen=screen)


def extract_tags(text: str) -> tuple[str, list[PointTag]]:
    """Non-streaming helper: return the speakable text and every point tag."""
    tags = [tag for tag in (parse_tag(m.group(0)) for m in TAG_RE.finditer(text)) if tag]
    return clean_spoken(CONTROL_RE.sub(" ", _MALFORMED_TAG_RE.sub(" ", TAG_RE.sub(" ", text)))), tags


def clean_spoken(text: str) -> str:
    """Collapse whitespace left behind by removed tags and strip markdown noise."""
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r" +([,.!?;:])", r"\1", text)
    return text.strip()


def split_sentences(text: str) -> tuple[list[str], str]:
    """Split off complete sentences; return them and the unfinished tail."""
    sentences, last = [], 0
    for match in _SENTENCE_END_RE.finditer(text):
        sentences.append(text[last:match.end()])
        last = match.end()
    return sentences, text[last:]


class ReplyStream:
    """Incrementally turn streamed model text into ordered speech/point events."""

    def __init__(self, *, min_chunk: int = 24) -> None:
        self.min_chunk = min_chunk
        self._raw = ""                 # may hold an incomplete "[POINT:..." prefix
        self._text = ""                # tag-free text not yet released
        self._pending_tags: list[PointTag] = []
        self.spoken: list[str] = []
        self.tags: list[PointTag] = []
        self.steps: int | None = None      # set by [STEPS:n]
        self.done = False                  # set by [DONE]
        self.actions: list[ActionTag] = []
        self.plan: tuple[str, ...] = ()
        self.goal = ""                     # set by [GOAL: ...]
        self.leaked = False                # it wrote a tool call out as text: nothing from there on is said
        self._thinking = False             # inside a long <thinking> block: nothing until it closes

    @property
    def spoken_text(self) -> str:
        return " ".join(self.spoken)

    def feed(self, delta: str) -> list[Event]:
        if self.leaked:
            return []                                # it was waiting for a tool's result: none of it is said
        raw, self._raw = self._raw + delta, ""
        if self._thinking:
            end = _THINKING_END_RE.search(raw)
            if end is None:
                self._raw = raw[-24:]                # the closing tag may be split across chunks
                return []
            raw, self._thinking = raw[end.end():], False
        events: list[Event] = []
        while raw:
            start = _next_special(raw)
            if start == -1:
                self._text += raw
                break
            self._text += raw[:start]
            raw = raw[start:]
            if raw[0] == "<":
                rest = self._angle(raw, events)
                if rest is None:
                    break                            # it may still turn into markup: wait for more
                raw = rest
                continue
            special = raw[:12].upper().replace(" ", "")
            if special.startswith(("[DO:", "[PLAN:", "[GOAL:")):
                scanned = scan_special(raw)
                if scanned is None:
                    if len(raw) > _MAX_ACTION_LEN:       # runaway tag: drop it
                        raw = ""
                        break
                    self._raw = raw
                    break
                consumed, tag = scanned
                raw = raw[consumed:]
                if tag is not None:
                    events.extend(self._release(merge=False))
                    if isinstance(tag, ActionTag):
                        self.actions.append(tag)
                    elif isinstance(tag, GoalTag):
                        self.goal = tag.text
                    else:
                        self.plan = tag.steps
                    events.append(tag)
                continue
            end = raw.find("]")
            inner = raw.find("[", 1)
            leak = _LEAK_RE.search(raw, 1)
            if leak is not None and (inner == -1 or leak.start() < inner):
                inner = leak.start()                 # "[note <invoke …": the markup counts, not the bracket
            if inner != -1 and (end == -1 or inner < end):
                # "array[0 ... [POINT:..]": the first bracket never closed, so it
                # is prose; restart at the next bracket so the tag still parses.
                self._text += raw[:inner]
                raw = raw[inner:]
                continue
            if end == -1:
                if len(raw) > _MAX_TAG_LEN:          # stray bracket, not a tag
                    self._text += raw[0]
                    raw = raw[1:]
                    continue
                self._raw = raw                      # wait for the rest of the tag
                break
            candidate, raw = raw[:end + 1], raw[end + 1:]
            control = CONTROL_RE.fullmatch(candidate)
            if control:
                events.extend(self._release(merge=False))
                events.append(StepsTag(int(control.group("steps"))) if control.group("steps") else DoneTag())
                if control.group("done"):
                    self.done = True
                else:
                    self.steps = int(control.group("steps"))
                continue
            if TAG_RE.fullmatch(candidate):
                # Sentences finished before the tag are spoken before it moves,
                # even short ones that would otherwise wait to be merged.
                events.extend(self._release(merge=False))
                tag = parse_tag(candidate)
                if tag:
                    self.tags.append(tag)
                    self._pending_tags.append(tag)
            elif not _MALFORMED_TAG_RE.fullmatch(candidate):
                self._text += candidate
            # A malformed "[POINT ...]" is dropped: it must never be read aloud.
        events.extend(self._release())
        return events

    def close(self) -> list[Event]:
        """Flush everything left at the end of the stream."""
        events: list[Event] = []
        if self._thinking:
            self._raw = ""                           # a thinking block that never closed: none of it is said
        for _ in range(32):
            leftover, self._raw = self._raw, ""
            if not leftover:
                break
            if leftover[0] == "<":
                rest = self._angle(leftover, events, final=True)
                if rest:
                    events.extend(self.feed(rest))
                continue
            if not re.match(r"\[\s*(?:POINT|STEPS|DONE|DO|PLAN|GOAL)\b", leftover, re.IGNORECASE):
                self._text += leftover
                break
            if re.match(r"\[\s*(?:DO|PLAN|GOAL)\b", leftover, re.IGNORECASE) and "]" in leftover:
                # A broken action tag: drop it and parse whatever came after it.
                events.extend(self.feed(leftover[leftover.find("]") + 1:]))
                continue
            break                                    # a truncated tag with nothing after it: drop it
        return events + self._release(final=True)

    def _angle(self, raw: str, events: list[Event], *, final: bool = False) -> str | None:
        """``<`` at the start of ``raw``: a tool call written out as text, a <thinking> block, or prose ("a < b")."""
        if _THINKING_RE.match(raw):
            end = _THINKING_END_RE.search(raw)
            if end is not None:
                return raw[end.end():]
            if final:
                return ""
            if len(raw) > _MAX_ACTION_LEN:           # a long one: stop buffering it, but stay quiet till it closes
                self._thinking, self._raw = True, raw[-24:]
                return ""
            self._raw = raw
            return None
        if _LEAK_RE.match(raw):
            events.extend(self._release(merge=False))  # what it said before the markup still counts
            self.leaked = True
            return ""
        if not final and len(raw) < 48 and ">" not in raw and "\n" not in raw:
            self._raw = raw                             # could still turn into markup
            return None
        self._text += raw[0]
        return raw[1:]

    def _release(self, *, final: bool = False, merge: bool = True) -> list[Event]:
        if final:
            sentences, self._text = [self._text], ""
        else:
            sentences, self._text = split_sentences(self._text)
            if merge:
                sentences = self._merge_short(sentences)
        events: list[Event] = []
        for sentence in sentences:
            cleaned = clean_spoken(sentence)
            if not cleaned:
                continue
            events.extend(self._pending_tags)
            self._pending_tags = []
            self.spoken.append(cleaned)
            events.append(SpeechChunk(cleaned))
        if final and self._pending_tags:
            events.extend(self._pending_tags)
            self._pending_tags = []
        return events

    def _merge_short(self, sentences: list[str]) -> list[str]:
        """Avoid tiny TTS requests ("Sure.") by merging into the next sentence."""
        merged: list[str] = []
        carry = ""
        for sentence in sentences:
            carry += sentence
            if len(carry.strip()) >= self.min_chunk:
                merged.append(carry)
                carry = ""
        if carry:
            self._text = carry + self._text
        return merged
