"""Provider-neutral conversation turns for the buddy.

Only the current turn carries screenshots. Earlier turns keep their text
(including the point tags the assistant emitted) so follow-ups like "and
where's the other one?" still make sense without re-sending old images.
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


@dataclass
class Conversation:
    max_turns: int = 10           # user+assistant messages kept, i.e. 5 exchanges
    turns: list[Turn] = field(default_factory=list)

    def history(self) -> list[Turn]:
        return list(self.turns)

    def record(self, user_text: str, assistant_text: str) -> None:
        if not user_text.strip() or not assistant_text.strip():
            return
        self.turns.extend([Turn("user", user_text), Turn("assistant", assistant_text)])
        overflow = len(self.turns) - self.max_turns
        if overflow > 0:
            # Drop whole exchanges so the history always starts with a user turn.
            overflow += overflow % 2
            del self.turns[:overflow]

    def clear(self) -> None:
        self.turns.clear()
