"""Safety policy. Restricted actions have a hard floor: no silent auto-run."""

from __future__ import annotations

import re

from mcp_vision.core.models import Policy, ScreenElement
from mcp_vision.log import get_logger

log = get_logger("mcp_vision.governor")

SAFE_READ_ACTIONS = frozenset({"inspect_screen"})
ROUTINE_ACTIONS = frozenset({"click_element", "type_text", "press_key_combination"})

_RESTRICTED_LABEL = re.compile(
    r"\b(password|passwd|pin|ssn|delete|trash|remove|uninstall|format|"
    r"buy|purchase|checkout|pay|terminal|iterm|sudo|root)\b",
    re.I,
)
_RESTRICTED_KEYS = {
    frozenset({"cmd", "q"}),
    frozenset({"command", "q"}),
    frozenset({"alt", "f4"}),
    frozenset({"cmd", "alt", "esc"}),
    frozenset({"ctrl", "alt", "del"}),
    frozenset({"cmd", "shift", "q"}),
}


def classify(
    action: str,
    element: ScreenElement | None = None,
    keys: list[str] | None = None,
    text: str = "",
) -> Policy:
    if action in SAFE_READ_ACTIONS:
        return Policy.SAFE_READ
    blob = " ".join([
        (element.label if element else ""),
        (element.text if element else ""),
        (element.role if element else ""),
        text,
    ]).lower()
    if element and element.role.lower() in {"textbox", "searchbox"} and "password" in blob:
        return Policy.RESTRICTED_ACTION
    if _RESTRICTED_LABEL.search(blob):
        return Policy.RESTRICTED_ACTION
    if keys:
        chord = frozenset(k.strip().lower() for k in keys)
        if chord in _RESTRICTED_KEYS:
            return Policy.RESTRICTED_ACTION
        if "terminal" in blob or action == "press_key_combination" and "sudo" in " ".join(keys).lower():
            return Policy.RESTRICTED_ACTION
    if action in ROUTINE_ACTIONS:
        return Policy.ROUTINE_WRITE
    return Policy.ROUTINE_WRITE


def requires_confirm(policy: Policy) -> bool:
    return policy is Policy.RESTRICTED_ACTION


class Governor:
    """Hard floor: restricted actions abort unless a confirmer returns True."""

    def __init__(self, confirmer: object | None = None) -> None:
        self.confirmer = confirmer  # Callable[[Policy, str], bool]

    def allow(self, policy: Policy, summary: str) -> bool:
        if not requires_confirm(policy):
            return True
        if self.confirmer is None:
            log.warning("RESTRICTED abort (no confirmer): %s", summary)
            return False
        try:
            ok = bool(self.confirmer(policy, summary))  # type: ignore[operator]
        except Exception as e:
            log.warning("confirmer failed, aborting: %s", e)
            return False
        if not ok:
            log.info("user aborted restricted action: %s", summary)
        return ok
