"""Fast pre-routing before the vision model runs.

The router answers small typed questions about a transcript in well under a
second: does this need a screenshot, and what kind of help is wanted? With a
TypeSafe key the Jev System-1 model answers them; otherwise conservative
rules do. Any doubt keeps the screenshot: a wasted image costs a little
latency, a missing one makes the buddy blind.
"""
from __future__ import annotations

import asyncio
import re
import time

from mcp_vision.buddy.companion import Route
from mcp_vision.buddy.geometry import ScreenInfo

_SCREEN_WORDS = re.compile(
    r"\b(this|that|these|those|here|there|it|my|screen|window|page|tab|app|button|menu|icon|field|"
    r"click|press|tap|open|close|select|find|where|see|look|show|point|highlight|which|"
    r"error|message|popup|dialog|setting|settings|option|toolbar|sidebar|file|folder|"
    r"document|doc|code|line|cell|row|column|chart|image|picture|photo|video|email|"
    r"how do i|how can i|how to|walk me|help me|what am i|what's on|what is on)\b",
    re.IGNORECASE,
)
_POINT_WORDS = re.compile(r"\b(where|point|show me|which (button|menu|icon|one)|find the|locate)\b", re.I)
_CHAT_WORDS = re.compile(r"^\s*(hi|hey|hello|thanks|thank you|good (morning|night|evening)|how are you)\b", re.I)
_GENERAL_LEADS = re.compile(
    r"^\s*(what('s| is| are| was| were)|who|when|why|define|tell me (a|about)|explain|how (many|much|far|long|old)|"
    r"translate|convert|calculate)\b",
    re.IGNORECASE,
)


def rule_route(transcript: str) -> Route:
    started = time.perf_counter()
    text = transcript.strip()
    if _CHAT_WORDS.match(text) and len(text.split()) <= 5:
        intent, needs_screen = "chat", False
    elif _POINT_WORDS.search(text):
        intent, needs_screen = "point", True
    elif _SCREEN_WORDS.search(text):
        intent, needs_screen = "explain", True
    elif _GENERAL_LEADS.match(text):
        intent, needs_screen = "answer", False
    else:
        intent, needs_screen = "explain", True
    return Route(needs_screen=needs_screen, intent=intent, provider="rules", confidence=0.6,
                 latency_ms=round((time.perf_counter() - started) * 1000, 2))


class RuleRouter:
    async def route(self, transcript: str, screens: list[ScreenInfo]) -> Route:
        return rule_route(transcript)


INTENTS = {
    "point": "The user wants to know where something is on their screen, or wants to be shown which "
             "control, menu, or setting to use.",
    "explain": "The user wants something that is on their screen explained, read, summarized, debugged, "
               "or reviewed.",
    "answer": "A general knowledge or how-the-world-works question that does not depend on what is on "
              "their screen.",
    "chat": "Small talk, a greeting, thanks, or a reaction, with no real question.",
}

NEEDS_SCREEN = (
    "The user is talking to a desktop assistant that can look at their screen. Would a good answer to "
    "what they said require seeing their screen right now? Yes for anything about what is on screen, "
    "where something is, how to do something in the app they are using, words like this, that, here, "
    "or it, errors, or their own documents, code, or email. No for general knowledge, small talk, or "
    "questions that are fully answerable without seeing the screen."
)


class JevRouter:
    """One Jev request answers every routing question in parallel (~100 ms)."""

    def __init__(self, client, *, screen_threshold: float = 0.35, fallback: RuleRouter | None = None,
                 timeout: float = 0.8):
        self.client = client
        self.screen_threshold = screen_threshold
        self.fallback = fallback or RuleRouter()
        self.timeout = timeout          # routing sits before Claude: never let it stall a turn

    def questions(self, multi_screen: bool) -> dict:
        from mcp_vision.buddy.jev import Choice, Noul

        questions = {
            "needs_screen": Noul(instructions=NEEDS_SCREEN),
            "intent": Choice(criteria=INTENTS, instructions="What kind of help does the user want?"),
            "depth": Choice(criteria={
                "quick": "One or two spoken sentences fully answer it.",
                "detailed": "It needs a step-by-step walkthrough or a longer explanation.",
            }, instructions="How long should a helpful spoken answer be?"),
        }
        if multi_screen:
            questions["scope"] = Choice(criteria={
                "cursor": "Only the screen the user is pointing at or working on matters.",
                "all": "The request mentions another monitor, both screens, or something that may be elsewhere.",
            }, instructions="The user has several monitors. Which screens are relevant?")
        return questions

    async def route(self, transcript: str, screens: list[ScreenInfo]) -> Route:
        started = time.perf_counter()
        multi = len(screens) > 1
        state = {"user_said": transcript, "monitors": len(screens) or 1}
        try:
            result = await asyncio.wait_for(self.client.ask(state, self.questions(multi)), self.timeout)
            needs = result.noul("needs_screen")
            intent = result.choice("intent", set(INTENTS))
            depth = result.choice("depth", {"quick", "detailed"})
            scope = result.choice("scope", {"cursor", "all"}) if multi else None
        except Exception:
            fallback = await self.fallback.route(transcript, screens)
            return Route(**{**fallback.__dict__, "provider": "rules (jev unavailable)"})
        needs_screen = needs >= self.screen_threshold or intent.choice in {"point", "explain"}
        return Route(
            needs_screen=needs_screen, intent=intent.choice,
            cursor_screen_only=bool(scope and scope.choice == "cursor" and scope.confidence >= 0.6),
            detailed=depth.choice == "detailed" and depth.p("detailed") >= 0.6,
            provider="jev", confidence=intent.confidence,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )
