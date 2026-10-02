"""Parse the model's pointing tags out of a streamed reply.

The model answers in plain spoken prose and may embed tags such as::

    [POINT:640,412:Export button]
    [POINT:220,96:File menu:screen2]
    [POINT:none]

Coordinates are pixels in the screenshot of the named screen (the cursor
screen when omitted). Tags never reach the speech engine. Each tag is
attached to the sentence it appears in and released just before that
sentence, so the buddy starts flying as the sentence that mentions the
control is spoken - without chopping the sentence in two.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TAG_RE = re.compile(
    r"\[POINT:\s*(?:"
    r"(?P<none>none)"
    r"|(?P<x>-?\d+(?:\.\d+)?)\s*,\s*(?P<y>-?\d+(?:\.\d+)?)"
    r"(?:\s*:\s*(?P<label>[^\]]*?))?"
    r")\s*\]",
    re.IGNORECASE,
)
_SCREEN_SUFFIX_RE = re.compile(r"^(?P<label>.*?)(?:\s*:\s*screen\s*(?P<screen>\d+))?\s*$", re.IGNORECASE | re.S)
_SENTENCE_END_RE = re.compile(r"[.!?…]+[\"'”’)\]]*\s+")
_MAX_TAG_LEN = 200


@dataclass(frozen=True)
class PointTag:
    x: float
    y: float
    label: str = ""
    screen: int | None = None        # 1-based; None means the cursor screen


@dataclass(frozen=True)
class SpeechChunk:
    text: str


Event = SpeechChunk | PointTag


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
    return clean_spoken(TAG_RE.sub(" ", text)), tags


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

    @property
    def spoken_text(self) -> str:
        return " ".join(self.spoken)

    def feed(self, delta: str) -> list[Event]:
        raw, self._raw = self._raw + delta, ""
        events: list[Event] = []
        while raw:
            start = raw.find("[")
            if start == -1:
                self._text += raw
                break
            self._text += raw[:start]
            raw = raw[start:]
            end = raw.find("]")
            if end == -1:
                if len(raw) > _MAX_TAG_LEN:          # stray bracket, not a tag
                    self._text += raw[0]
                    raw = raw[1:]
                    continue
                self._raw = raw                      # wait for the rest of the tag
                break
            candidate, raw = raw[:end + 1], raw[end + 1:]
            if TAG_RE.fullmatch(candidate):
                # Sentences finished before the tag are spoken before it moves,
                # even short ones that would otherwise wait to be merged.
                events.extend(self._release(merge=False))
                tag = parse_tag(candidate)
                if tag:
                    self.tags.append(tag)
                    self._pending_tags.append(tag)
            else:
                self._text += candidate
        events.extend(self._release())
        return events

    def close(self) -> list[Event]:
        """Flush everything left at the end of the stream."""
        leftover, self._raw = self._raw, ""
        self._text += leftover
        return self._release(final=True)

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
