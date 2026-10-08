"""Provider-neutral conversation turns for the buddy.

Only the current turn carries screenshots. Earlier turns keep their text
(including the point tags the assistant emitted) so follow-ups like "and
where's the other one?" still make sense without re-sending old images.

History rides along on every model call, so it stays lean: when a multi-step
request finishes, its steps fold into one exchange.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from mcp_vision.buddy.geometry import Screenshot


@dataclass(frozen=True)
class Turn:
    role: Literal["user", "assistant"]
    text: str
    images: tuple[Screenshot, ...] = ()
    request: int = 0              # which of the user's requests it belongs to (a task's steps share one)


@dataclass
class Conversation:
    max_turns: int = 10           # user+assistant messages kept, i.e. 5 exchanges
    turns: list[Turn] = field(default_factory=list)
    requests: int = 0             # user requests recorded so far

    def history(self) -> list[Turn]:
        return list(self.turns)

    def record(self, user_text: str, assistant_text: str, *, step: bool = False) -> None:
        """One exchange. ``step``: Plip's own follow-up during a request (action results, a walkthrough check-in)."""
        if not user_text.strip() or not assistant_text.strip():
            return
        if not step or not self.requests:
            self.requests += 1
        self.turns.extend([Turn("user", user_text, request=self.requests),
                           Turn("assistant", assistant_text, request=self.requests)])
        overflow = len(self.turns) - self.max_turns
        if overflow > 0:
            # Drop a few exchanges at once, not one per turn: the history is the start of every prompt, and
            # a start that shifts each turn is never read back from the prompt cache. Whole exchanges, so it
            # always starts with a user turn.
            overflow = max(overflow, self.max_turns // 4)
            overflow += overflow % 2
            del self.turns[:overflow]

    def fold(self) -> None:
        """The latest request is done: its steps become one exchange.

        What they asked stays, and so does everything Plip said along the way (with what it did and
        where it pointed); the step-by-step results it was handed (search hits, page text) go.
        """
        mine = [turn for turn in self.turns if turn.request == self.requests]
        if len(mine) <= 2 or mine[0].role != "user":
            return
        said = " ".join(turn.text for turn in mine if turn.role == "assistant")
        self.turns = [turn for turn in self.turns if turn.request != self.requests]
        self.turns += [mine[0], Turn("assistant", said, request=self.requests)]

    def clear(self) -> None:
        self.turns.clear()
