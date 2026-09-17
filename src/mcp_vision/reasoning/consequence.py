"""Consequence / risk model and shadow-consequence reasoning.

Autonomy should decrease as consequences increase. Research and comparison are
aggressive; anything with an external effect or that is expensive/destructive
gets gated. The runtime governors stay the hard floor (permissions, identity,
verification); this layer only *assesses* and *surfaces*.
"""
from __future__ import annotations

import re

from mcp_vision.reasoning.schemas import ConsequenceLevel, ProposedAction

# Coarse signals for the light path; the model reasons about semantics on top.
_EXTERNAL = re.compile(
    r"\b(send|submit|book|buy|purchase|order|pay|checkout|apply|post|publish|"
    r"transfer|wire|delete|trash|remove|rm|destroy|format|uninstall|create\s+account|"
    r"sign[\s-]?up|subscribe|authorize|compose)\b",
    re.I,
)
_IDENTITY = re.compile(r"\b(password|ssn|identity|birth date)\b", re.I)
# "why is checkout broken / fix X / figure out" is research, however payment-ish
# the subject words look. Research/diagnosis is never itself a commit.
_RESEARCH_FRAMING = re.compile(
    r"\b(figure out|find out|why(?: is| does| do| did)|fix|repair|debug|diagnos|"
    r"not working|broken|error|troubleshoot|investigate)\b", re.I)


def level_for_request(text: str) -> ConsequenceLevel:
    """Best-effort consequence classification for a raw user request (fast path).

    Only the *action* a request asks for confers consequence, not its subject.
    Reading/researching about 'checkout' is safe; buying something is not.
    """
    low = (text or "").lower()
    if _IDENTITY.search(low):
        return ConsequenceLevel.CRITICAL
    if _RESEARCH_FRAMING.search(low):
        return ConsequenceLevel.NONE
    if re.search(r"\b(buy|purchase|order|pay|checkout|transfer|wire|delete|destroy|format)\b", low):
        return ConsequenceLevel.SIGNIFICANT
    if _EXTERNAL.search(low):
        return ConsequenceLevel.SIGNIFICANT
    if re.search(r"\b(organize|move|rename|sort|clean|fill|draft)\b", low):
        return ConsequenceLevel.RECOVERABLE
    return ConsequenceLevel.NONE


def level_for_action(action: str) -> ConsequenceLevel:
    """Consequence of a single proposed action, by action type."""
    return {
        "search": ConsequenceLevel.NONE,
        "inspect": ConsequenceLevel.NONE,
        "read": ConsequenceLevel.NONE,
        "list": ConsequenceLevel.NONE,
        "compare": ConsequenceLevel.NONE,
        "draft": ConsequenceLevel.MINOR,
        "organize": ConsequenceLevel.RECOVERABLE,
        "rename": ConsequenceLevel.RECOVERABLE,
        "move": ConsequenceLevel.RECOVERABLE,
        "fill": ConsequenceLevel.RECOVERABLE,
        "click": ConsequenceLevel.MINOR,
        "select": ConsequenceLevel.MINOR,
        "send": ConsequenceLevel.SIGNIFICANT,
        "submit": ConsequenceLevel.SIGNIFICANT,
        "apply": ConsequenceLevel.SIGNIFICANT,
        "buy": ConsequenceLevel.SIGNIFICANT,
        "delete": ConsequenceLevel.SIGNIFICANT,
        "destroy": ConsequenceLevel.CRITICAL,
    }.get(action, ConsequenceLevel.MINOR)


def autonomy_allowed(action_level: ConsequenceLevel, *, gate: ConsequenceLevel = ConsequenceLevel.NONE) -> bool:
    """Whether the system may proceed on its own at this consequence level.

    `gate` is the caller's autonomy ceiling (often the harness's configured cap).
    At or below the cap the system may act; above it, it must pause.
    """
    return int(action_level) <= int(gate)


def shadow_consequences(action: ProposedAction, objective: str = "") -> list[str]:
    """Semantic side effects a successful action may have, beyond the click.

    Predict before acting: 'click Apply' can submit, notify, agree to terms,
    create an account, charge a card. Surface these so the gate can decide.
    """
    shadows: list[str] = []
    blob = " ".join(
        [action.action.lower()] + [str(v).lower() for v in action.params.values()] + [objective.lower()]
    )
    if re.search(r"\b(send|submit|apply|post|compose|transfer)\b", blob):
        shadows.append("may send/submit/publish to an external party")
    if re.search(r"\b(buy|purchase|order|checkout|pay|subscribe)\b", blob):
        shadows.append("may create a charge / obligation")
    if re.search(r"\b(delete|trash|remove|destroy|format|uninstall)\b", blob):
        shadows.append("may permanently remove data")
    if re.search(r"\b(create\s?account|sign[\s-]?up|sign\s?in|login)\b", blob):
        shadows.append("may create/enter an identity or account")
    if action.expected and not action.reversible and int(action.consequence) >= int(ConsequenceLevel.SIGNIFICANT):
        shadows.append("not reversible at this consequence level")
    return shadows


_COMMIT_SHADOW = re.compile(
    r"\b(send/submit|charge|obligation|permanently remove|identity|account)\b", re.I)


def escalate_for_shadows(level: int, shadows: list[str]) -> int:
    """Raise a consequence estimate if a shadow signals an external effect that
    the bare action level may miss (e.g. 'click' on an 'Apply' button).

    Read-only research never escalates; commit-adjacent actions do. This is how
    the harness stays aggressive about investigating while still gating real
    submissions/purchases/deletions.
    """
    if any(_COMMIT_SHADOW.search(s or "") for s in shadows):
        return max(int(level), int(ConsequenceLevel.SIGNIFICANT))
    return int(level)