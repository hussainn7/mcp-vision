"""Hybrid visual-DOM grounding.

Prefer Playwright AX/DOM boxes (exact, occlusion-probed). When those aren't
available — canvas, native desktop, empty hydration — fall back to SoM
visual boxes and map them through the viewport transform so the click
lands in CSS/logical space, not screenshot pixels.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from phase1_vision.coords import Viewport, clamp_point, normalize_box, screenshot_to_click
from phase1_vision.som import detect_ui_boxes, parse_som_id, to_elements


@dataclass
class Grounded:
    source: str  # "dom" | "visual"
    index: int
    x: int
    y: int
    box: dict
    confidence: float
    label: str = ""
    role: str = ""


@dataclass
class GroundingResult:
    mode: str  # "dom" | "visual" | "hybrid"
    elements: list[dict] = field(default_factory=list)
    grounded: list[Grounded] = field(default_factory=list)


def from_dom(snapshot: dict | None, vp: Viewport | None = None) -> list[dict]:
    """Lift a page_snapshot payload into canonical boxes in CSS viewport space."""
    if not snapshot:
        return []
    vp = vp or Viewport(css_w=1, css_h=1, screenshot_w=1, screenshot_h=1)
    out = []
    for e in snapshot.get("elements") or []:
        box = normalize_box(e, vp, space="css")
        box["id"] = int(e.get("index", e.get("id", len(out))))
        box["label"] = e.get("name") or e.get("label") or ""
        box["role"] = e.get("role") or ""
        box["cx"] = int(e.get("cx", box["cx"]))
        box["cy"] = int(e.get("cy", box["cy"]))
        out.append(box)
    return out


def from_visual(img, vp: Viewport, max_boxes: int = 40) -> list[dict]:
    """Heuristic SoM boxes in screenshot space, remapped to CSS viewport."""
    raw = detect_ui_boxes(img, max_boxes=max_boxes)
    out = []
    for b in raw:
        box = normalize_box(b, vp, space="screenshot")
        box["id"] = int(b.get("id", len(out)))
        box["label"] = b.get("label") or f"region {box['id']}"
        box["role"] = "visual"
        out.append(box)
    return out


def resolve(target, dom_boxes: list[dict], visual_boxes: list[dict] | None = None,
            vp: Viewport | None = None) -> Grounded | None:
    """Pick a click point for `target` (int index, or a string the model spat out).

    DOM wins on index match. Visual is the fallback when DOM is empty or the
    index isn't in the last snapshot (stale after a re-render).
    """
    idx = target if isinstance(target, int) else parse_som_id(str(target))
    if idx is None:
        return None
    vp = vp or Viewport(css_w=1920, css_h=1080, screenshot_w=1920, screenshot_h=1080)
    for src, boxes, conf in (("dom", dom_boxes, 0.95), ("visual", visual_boxes or [], 0.55)):
        for b in boxes:
            if int(b.get("id", -1)) == idx:
                x, y = clamp_point(b.get("cx", b["x"] + b["w"] / 2),
                                   b.get("cy", b["y"] + b["h"] / 2), vp)
                return Grounded(source=src, index=idx, x=int(x), y=int(y),
                                box=b, confidence=conf,
                                label=str(b.get("label") or ""),
                                role=str(b.get("role") or ""))
    return None


def click_point(g: Grounded, vp: Viewport, backend: str = "playwright") -> tuple[int, int]:
    """Map a grounded CSS point to the coordinate space the backend clicks in."""
    if backend == "playwright":
        return g.x, g.y
    return screenshot_to_click(g.x * vp.sx, g.y * vp.sy, vp)


def hybrid(snapshot: dict | None, img=None, vp: Viewport | None = None) -> GroundingResult:
    """DOM boxes when present; visual SoM when the page has no reachable DOM."""
    vp = vp or Viewport(css_w=1280, css_h=800, screenshot_w=1280, screenshot_h=800)
    dom = from_dom(snapshot, vp)
    if dom:
        return GroundingResult(mode="dom", elements=to_elements(dom),
                               grounded=[resolve(b["id"], dom, vp=vp) for b in dom])
    vis = from_visual(img, vp) if img is not None else []
    return GroundingResult(mode="visual" if vis else "dom",
                           elements=to_elements(vis),
                           grounded=[resolve(b["id"], [], vis, vp) for b in vis])


def demo():
    vp = Viewport(css_w=1280, css_h=800, screenshot_w=2560, screenshot_h=1600, dpr=2.0)
    snap = {"elements": [
        {"index": 3, "role": "link", "name": "Pricing", "cx": 80, "cy": 20,
         "x": 40, "y": 10, "w": 80, "h": 20},
    ]}
    dom = from_dom(snap, vp)
    assert dom[0]["id"] == 3 and dom[0]["cx"] == 80
    g = resolve(3, dom, vp=vp)
    assert g and g.source == "dom" and g.x == 80 and g.y == 20 and g.confidence > 0.9
    assert resolve("[3] click pricing", dom, vp=vp).index == 3
    assert resolve(99, dom, vp=vp) is None

    # visual fallback: retina screenshot box maps back to CSS
    vis = [normalize_box({"x": 160, "y": 40, "w": 160, "h": 40, "id": 0, "label": "btn"},
                         vp, space="screenshot")]
    vis[0]["id"] = 0
    g2 = resolve(0, [], vis, vp)
    assert g2 and g2.source == "visual"
    assert g2.x == vis[0]["cx"] == 120 and g2.y == vis[0]["cy"] == 30

    # playwright backend keeps CSS; pyautogui goes through screenshot_to_click
    assert click_point(g, vp, "playwright") == (80, 20)

    # hybrid prefers DOM and does not run the detector
    r = hybrid(snap, img=None, vp=vp)
    assert r.mode == "dom" and r.elements[0]["id"] == 3

    r2 = hybrid({"elements": []}, img=None, vp=vp)
    assert r2.mode == "dom" and r2.elements == []
    print("ok")


if __name__ == "__main__":
    demo()
