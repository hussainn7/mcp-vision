from __future__ import annotations

from mcp_vision.core.governor import Governor, classify, requires_confirm
from mcp_vision.core.models import BoundingBox, Policy, ScreenElement


def _el(label: str, role: str = "button") -> ScreenElement:
    return ScreenElement(
        id=1, label=label, role=role,
        bbox=BoundingBox(x=0, y=0, w=10, h=10), cx=5, cy=5,
    )


def test_read_is_safe() -> None:
    assert classify("inspect_screen") is Policy.SAFE_READ
    assert not requires_confirm(Policy.SAFE_READ)


def test_password_and_delete_are_restricted() -> None:
    assert classify("click_element", element=_el("Password", "textbox")) is Policy.RESTRICTED_ACTION
    assert classify("click_element", element=_el("Delete account")) is Policy.RESTRICTED_ACTION
    assert classify("click_element", element=_el("Buy now")) is Policy.RESTRICTED_ACTION


def test_routine_click_and_save() -> None:
    assert classify("click_element", element=_el("Search")) is Policy.ROUTINE_WRITE
    assert classify("press_key_combination", keys=["cmd", "s"]) is Policy.ROUTINE_WRITE
    assert classify("press_key_combination", keys=["cmd", "q"]) is Policy.RESTRICTED_ACTION


def test_hard_floor_aborts_without_confirmer() -> None:
    g = Governor()
    assert not g.allow(Policy.RESTRICTED_ACTION, "quit")


def test_confirmer_true_allows_restricted() -> None:
    g = Governor(confirmer=lambda _p, _s: True)
    assert g.allow(Policy.RESTRICTED_ACTION, "quit")


def test_confirmer_false_is_abort() -> None:
    g = Governor(confirmer=lambda _p, _s: False)
    assert not g.allow(Policy.RESTRICTED_ACTION, "quit")
