"""CDP Accessibility.getFullAXTree → numbered interactive elements + crops.

Geometry comes from the AX tree / box model, not a full-screen screenshot.
The LLM gets a short index list; vision only sees a localized badge crop.
"""

from __future__ import annotations

from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from phase2_mcp.page_snapshot import crop_box

INTERACTIVE_ROLES = {
    "button", "link", "textbox", "searchbox", "combobox", "checkbox",
    "radio", "menuitem", "menuitemcheckbox", "menuitemradio", "tab",
    "slider", "switch", "spinbutton", "option", "treeitem",
}


def _role(node: dict) -> str:
    r = node.get("role")
    if isinstance(r, dict):
        return str(r.get("value") or "").lower()
    return str(r or "").lower()


def _name(node: dict) -> str:
    n = node.get("name")
    if isinstance(n, dict):
        return str(n.get("value") or "").replace("\n", " ").strip()[:100]
    return str(n or "").replace("\n", " ").strip()[:100]


def flatten_ax_nodes(nodes: list[dict], max_elements: int = 60) -> list[dict]:
    """Keep interactive, non-ignored AX nodes (no geometry yet)."""
    out = []
    for node in nodes or []:
        if node.get("ignored"):
            continue
        role = _role(node)
        if role not in INTERACTIVE_ROLES:
            continue
        name = _name(node)
        backend = node.get("backendDOMNodeId")
        out.append({
            "role": role,
            "name": name,
            "backendDOMNodeId": backend,
            "nodeId": node.get("nodeId"),
        })
        if len(out) >= max_elements:
            break
    return out


def box_model_to_rect(model: dict) -> Optional[dict]:
    """DOM.getBoxModel content quad → x,y,w,h,cx,cy."""
    content = (model or {}).get("content") or (model or {}).get("border")
    if not content or len(content) < 8:
        return None
    xs = content[0::2]
    ys = content[1::2]
    x, y = min(xs), min(ys)
    w, h = max(xs) - x, max(ys) - y
    if w <= 0 or h <= 0:
        return None
    return {
        "x": x, "y": y, "w": w, "h": h,
        "cx": x + w / 2, "cy": y + h / 2,
    }


def stamp_badges(img: Image.Image, elements: list[dict], origin=(0, 0)) -> Image.Image:
    """Draw numeric badges on a crop. origin is the crop's top-left in page CSS px."""
    out = img.copy()
    draw = ImageDraw.Draw(out)
    ox, oy = origin
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    for el in elements:
        cx = int(el["cx"] - ox)
        cy = int(el["cy"] - oy)
        idx = el.get("index", 0)
        r = 10
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(220, 40, 40), outline=(255, 255, 255))
        label = str(idx)
        if font:
            draw.text((cx - 4, cy - 6), label, fill=(255, 255, 255), font=font)
        else:
            draw.text((cx - 4, cy - 6), label, fill=(255, 255, 255))
    return out


def union_clip(elements: list[dict], viewport: tuple[int, int] | None, pad: int = 16) -> Optional[dict]:
    """One clip covering all badges — localized framebuffer, not full screen."""
    if not elements:
        return None
    x0 = min(e["x"] for e in elements)
    y0 = min(e["y"] for e in elements)
    x1 = max(e["x"] + e["w"] for e in elements)
    y1 = max(e["y"] + e["h"] for e in elements)
    fake = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
    return crop_box(fake, pad=pad, min_size=96, max_size=900, viewport=viewport)


def demo():
    nodes = [
        {"ignored": True, "role": {"value": "button"}, "name": {"value": "hidden"}},
        {"role": {"value": "button"}, "name": {"value": "Compose"}, "backendDOMNodeId": 1},
        {"role": {"value": "generic"}, "name": {"value": "skip me"}},
        {"role": {"value": "link"}, "name": {"value": "Inbox"}, "backendDOMNodeId": 2},
        {"role": {"value": "textbox"}, "name": {"value": "Search"}, "backendDOMNodeId": 3},
    ]
    els = flatten_ax_nodes(nodes)
    assert [e["name"] for e in els] == ["Compose", "Inbox", "Search"]

    rect = box_model_to_rect({"content": [10, 20, 50, 20, 50, 40, 10, 40]})
    assert rect["x"] == 10 and rect["y"] == 20 and rect["w"] == 40 and rect["h"] == 20
    assert rect["cx"] == 30 and rect["cy"] == 30

    img = Image.new("RGB", (200, 100), (240, 240, 240))
    stamped = stamp_badges(img, [{"index": 0, "cx": 40, "cy": 40, "x": 30, "y": 30, "w": 20, "h": 20}])
    assert stamped.size == (200, 100)
    assert stamped.getpixel((40, 40))[0] > 150  # red badge

    clip = union_clip(
        [{"x": 100, "y": 80, "w": 40, "h": 20, "cx": 120, "cy": 90}],
        viewport=(1280, 800),
    )
    assert clip and clip["width"] >= 96
    print("ok")


if __name__ == "__main__":
    demo()
