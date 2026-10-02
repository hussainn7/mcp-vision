"""Fill the form on screen with what Plip knows about you.

The model sees the screen map (every text field with its position) and your
saved details, and answers with ``fill_form`` listing field → value. Plip
shows the plan in the island, waits for your OK, then clicks each field and
types the value. It never presses submit.
"""
from __future__ import annotations

import math
import time

from mcp_vision.buddy.actions.base import ActionContext, ActionError, ActionResult, ActionSpec, Preview

FIELD_ROLES = {"text field", "text area", "search field", "combobox", "combo box", "AXTextField", "AXTextArea",
               "AXComboBox", "AXSearchField"}
SNAP_RADIUS = 48.0            # points
MAX_FIELDS = 30


def _shots_and_context(ctx: ActionContext):
    shots, context = ctx.screen if isinstance(ctx.screen, tuple) else ([], None)
    return list(shots or []), context


def resolve_fields(ctx: ActionContext, fields: list) -> list[dict]:
    """Model fields (screenshot pixels) -> global points, snapped onto real text fields."""
    shots, context = _shots_and_context(ctx)
    if not shots:
        raise ActionError("I need to look at the form first. Ask me again with it on screen.")
    text_fields = [control for control in (context.controls if context else []) if control.role in FIELD_ROLES]
    resolved = []
    for raw in fields[:MAX_FIELDS]:
        if not isinstance(raw, dict):
            continue
        value = str(raw.get("value") or "").strip()
        if not value or len(value) > 500:
            continue
        try:
            px, py = float(raw["x"]), float(raw["y"])
        except (KeyError, TypeError, ValueError):
            continue
        screen = raw.get("screen")
        shot = next((item for item in shots if screen and item.screen.index == int(screen)), None) or \
            next((item for item in shots if item.screen.is_cursor_screen), shots[0])
        gx, gy = shot.to_global(px, py)
        label = str(raw.get("label") or "").strip()
        nearest = min(text_fields, key=lambda control: math.hypot(control.x - gx, control.y - gy), default=None)
        if nearest is not None and math.hypot(nearest.x - gx, nearest.y - gy) <= SNAP_RADIUS:
            gx, gy = nearest.x, nearest.y
            label = label or nearest.label
        resolved.append({"x": gx, "y": gy, "label": label or "field", "value": value})
    return resolved


def preview_fill(ctx: ActionContext, args: dict) -> Preview:
    fields = resolve_fields(ctx, args.get("fields") or [])
    if not fields:
        raise ActionError("I didn't find any fields I could fill with what I know about you.")
    lines = [f"{field['label']} → {field['value']}" for field in fields]
    noun = "field" if len(fields) == 1 else "fields"
    return Preview(title=f"Fill {len(fields)} {noun}", lines=lines, confirm="Fill it in", state=fields)


def fill_form(ctx: ActionContext, args: dict, fields: list | None = None) -> ActionResult:
    fields = fields if fields is not None else resolve_fields(ctx, args.get("fields") or [])
    filled = 0
    for field in fields:
        if ctx.host.set_field(field["x"], field["y"], field["value"]):
            filled += 1
        time.sleep(0.08)
    noun = "field" if filled == 1 else "fields"
    return ActionResult(say=f"Filled in {filled} {noun}. Give it a quick look before you submit.",
                        detail=f"{filled} {noun} filled")


SPECS = (
    ActionSpec("fill_form", "forms", "Filling the form", fill_form, preview=preview_fill,
               args='{"fields": [{"x", "y", "label", "value"}]}'),
)
