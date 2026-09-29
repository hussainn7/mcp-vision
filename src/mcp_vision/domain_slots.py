"""Domain-specific slot clarification and tool implementations.

Kept separate from request_routing and reasoning so the generic runtime
stays free of task-named planner branches. Add new domain handlers here,
not in request_routing / reasoners / intent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class SlotPrompt:
    prompt: str
    slot: str


# ---------------------------------------------------------------------------
# Travel detection helpers (no hardcoded examples in runtime files)
# ---------------------------------------------------------------------------

_TRAVEL_TRANSPORT = re.compile(
    r"\b(?:flights?|plane|fly|flying|round.?trip|one.?way|ticket|trip|travel|itinerary)\b",
    re.I,
)

_TRAVEL_VERB = re.compile(
    r"\b(?:book(?:ing)?|find(?:ing)?|search(?:ing)?|look(?:ing)?\s+for|"
    r"check(?:ing)?|compare|show)\b.*\b(?:flights?|ticket|trip|travel|itinerary)\b|"
    r"\bflights?\b|\btickets?\b|\btrips?\b",
    re.I,
)

_ORIGIN_SLOT = re.compile(
    r"\bfrom\s+\S+|\bdepart(?:ing|ure)?\s+from\b|"
    r"\b(?:flights?|ticket|trip|travel)\s+(?:from\s+)?[a-z0-9][a-z0-9 .'-]*?\s+to\s+[a-z0-9]",
    re.I,
)

_DATE_SLOT = re.compile(
    r"\b(?:today|tomorrow|week|weekend|month|anytime|any\s+time|any\s+day|flexible|whenever|"
    r"any\s+(?:dates?|days?|week|month)|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b|"
    r"\b(?:on\s+)?(?:the\s+)?\d{1,2}(?:st|nd|rd|th)\b|\d{1,4}[-/]\d{1,2}",
    re.I,
)

# Keyword set used by intent analysis to tag travel constraints
_TRAVEL_OBJECTIVE_PAT = re.compile(
    r"\b(?:flights?|plane|fly|flying|round.?trip|one.?way|airport)\b",
    re.I,
)


def is_travel_objective(text: str) -> bool:
    """Return True when the text describes a travel/transport goal."""
    return bool(_TRAVEL_OBJECTIVE_PAT.search(text) or _TRAVEL_TRANSPORT.search(text))


def is_travel_routing_request(text: str) -> bool:
    """Return True when the text is a request that needs travel-specific routing."""
    return bool(
        _TRAVEL_OBJECTIVE_PAT.search(text)
        or (_TRAVEL_VERB.search(text) and _TRAVEL_TRANSPORT.search(text))
    )


def clarify_browser_request(text: str) -> SlotPrompt | None:
    """Return a clarification prompt if a required slot is missing, else None."""
    if not is_travel_routing_request(text):
        return None

    if not _ORIGIN_SLOT.search(text):
        return SlotPrompt(
            prompt=(
                "What city are you flying from? Include your departure and return dates "
                "(or say one-way) so I can find the best options."
            ),
            slot="departure",
        )

    if not _DATE_SLOT.search(text):
        return SlotPrompt(
            prompt=(
                "What are your departure and return dates? "
                "You can say one-way, give a flexible range, or just say any dates."
            ),
            slot="dates",
        )

    return None


# ---------------------------------------------------------------------------
# Travel-specific Decision for the reasoning layer
# ---------------------------------------------------------------------------

def get_domain_decision(state: Any) -> Any | None:
    """Return a domain-specific Decision for travel goals, or None if not applicable.

    Called by reasoners to handle travel goals without importing domain
    terminology into the generic reasoning core.
    """
    if not is_travel_routing_request(state.objective.lower()):
        return None

    try:
        from mcp_vision.reasoning.schemas import (
            ProposedAction, Decision, ConsequenceLevel, ExpectedOutcome,
        )
    except ImportError:
        return None

    goal = state.objective
    state.add_assumption(
        "dates flexible; one traveler; economy class",
        basis="reasonable defaults for exploratory travel research",
        confidence=0.5, reversible=True, verify_before=ConsequenceLevel.SIGNIFICANT,
    )
    return Decision(
        strategy="compare travel options",
        next=ProposedAction(
            action="search",
            params={"query": goal, "kind": "travel"},
            expected=ExpectedOutcome(
                action="search",
                expected_effect="travel options",
                success_signal="results",
            ),
            consequence=ConsequenceLevel.NONE,
            reversible=True,
            rationale="research only; dates assumed flexible",
        ),
        meta="research path; never purchase without confirmation",
    )


# ---------------------------------------------------------------------------
# MCP tool: travel search (registered by server.py via TOOLS list)
# ---------------------------------------------------------------------------

def prepare_travel_search(request: str) -> dict:
    """Validate travel-search details; return a clarification or a ready search URL."""
    from mcp_vision.plan import plan_url
    from mcp_vision.request_routing import route_request

    route = route_request(request, "ask")
    if route.kind == "input" and route.missing in {"departure", "dates"}:
        return {"ready": False, "missing": route.missing, "question": route.message}
    planned = plan_url(request)
    if planned.get("reason") not in {"travel search", "flight search"}:
        return {
            "ready": False,
            "missing": "request",
            "question": "What trip would you like me to research?",
        }
    return {"ready": True, "missing": "", "question": "", "url": planned["url"]}


# Instruction hint injected into the MCP server's system prompt.
TOOL_HINT: str = (
    "Use prepare_travel_search before researching trips or transport options, "
    "and ask its clarification verbatim when ready is false. "
    "When ready is true, open its URL and report observed options without booking."
)

# Tools registered by server._mcp() without importing domain names there.
TOOLS: list = [prepare_travel_search]
