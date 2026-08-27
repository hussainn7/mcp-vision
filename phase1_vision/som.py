"""Set-of-Marks: numbered overlays + a Pillow-only UI detector fallback.

OmniParser/YOLO is optional. When it isn't installed we still produce a
numbered overlay from (a) Playwright DOM boxes or (b) a contrast-blob
detector that finds button-like rectangles. The overlay is what a VLM
sees; the id->box table is what the click path uses.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def draw_marks(img: Image.Image, boxes: list[dict], numbered: bool = True) -> Image.Image:
    """Paint numbered marks on a copy of `img`. Boxes use x,y,w,h in image pixels."""
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("Arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    for i, b in enumerate(boxes):
        x, y = int(b["x"]), int(b["y"])
        w, h = int(b.get("w", b.get("width", 0))), int(b.get("h", b.get("height", 0)))
        x2, y2 = x + w, y + h
        draw.rectangle([x, y, x2, y2], outline=(255, 0, 80), width=2)
        if not numbered:
            continue
        label = str(b.get("id", i))
        tw, th = _text_size(draw, label, font)
        pad = 2
        bx1, by1 = x, max(0, y - th - pad * 2)
        draw.rectangle([bx1, by1, bx1 + tw + pad * 2, by1 + th + pad * 2], fill=(255, 0, 80))
        draw.text((bx1 + pad, by1 + pad), label, fill=(255, 255, 255), font=font)
    return out


def _text_size(draw, text, font):
    if hasattr(draw, "textbbox"):
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        return r - l, b - t
    return draw.textsize(text, font=font)


def to_elements(boxes: list[dict], origin: int = 0) -> list[dict]:
    """Canonical element records: id, label, x/y center, bbox. Matches MCP tools."""
    out = []
    for i, b in enumerate(boxes):
        x, y = int(b["x"]), int(b["y"])
        w, h = int(b.get("w", b.get("width", 0))), int(b.get("h", b.get("height", 0)))
        eid = int(b.get("id", origin + i))
        out.append({
            "id": eid,
            "label": str(b.get("label") or b.get("name") or f"element {eid}"),
            "role": str(b.get("role") or "unknown"),
            "x": x + w // 2,
            "y": y + h // 2,
            "bbox": [x, y, x + w, y + h],
            "w": w,
            "h": h,
        })
    return out


def parse_som_id(text: str) -> int | None:
    """Pull a mark id out of model output like '[12] click the search box' or 'id=12'."""
    import re
    m = re.search(r"\[(\d+)\]", text) or re.search(r"\bid\s*=\s*(\d+)", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{1,3})\b", text)
    return int(m.group(1)) if m else None


def detect_ui_boxes(img: Image.Image, max_boxes: int = 40, min_size: int = 12, max_frac: float = 0.6) -> list[dict]:
    """Heuristic button/input detector. Contrast edges -> connected rectangles.

    Not YOLO. Good enough to ground a desktop click when the AX tree is gone
    (canvas, games, native apps with no DOM). Prefer DOM boxes when they exist.
    """
    rgb = img.convert("RGB")
    w, h = rgb.size
    gray = rgb.convert("L").filter(ImageFilter.FIND_EDGES)
    bw = gray.point(lambda p: 255 if p > 40 else 0)
    px = bw.load()
    visited = bytearray(w * h)
    boxes = []
    max_w, max_h = int(w * max_frac), int(h * max_frac)

    def idx(x, y):
        return y * w + x

    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if px[x, y] == 0 or visited[idx(x, y)]:
                continue
            stack = [(x, y)]
            visited[idx(x, y)] = 1
            minx = maxx = x
            miny = maxy = y
            n = 0
            while stack:
                cx, cy = stack.pop()
                n += 1
                if cx < minx:
                    minx = cx
                if cx > maxx:
                    maxx = cx
                if cy < miny:
                    miny = cy
                if cy > maxy:
                    maxy = cy
                for nx, ny in ((cx + 2, cy), (cx - 2, cy), (cx, cy + 2), (cx, cy - 2)):
                    if 0 <= nx < w and 0 <= ny < h and px[nx, ny] and not visited[idx(nx, ny)]:
                        visited[idx(nx, ny)] = 1
                        stack.append((nx, ny))
            bw_, bh_ = maxx - minx + 1, maxy - miny + 1
            if n < 8 or bw_ < min_size or bh_ < min_size:
                continue
            if bw_ > max_w or bh_ > max_h:
                continue
            ar = bw_ / bh_
            if ar > 12 or ar < 0.12:
                continue
            boxes.append({"x": minx, "y": miny, "w": bw_, "h": bh_, "label": "region"})

    boxes.sort(key=lambda b: (b["y"] // 20, b["x"]))
    # drop nested duplicates: keep the smaller of two heavily overlapping boxes
    kept = []
    for b in boxes:
        overlap = False
        for k in kept:
            if _iou(b, k) > 0.7:
                overlap = True
                break
        if not overlap:
            kept.append(b)
        if len(kept) >= max_boxes:
            break
    for i, b in enumerate(kept):
        b["id"] = i
    return kept


def _iou(a, b):
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union else 0.0


def save_elements(elements: list[dict], path: Path):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(elements, indent=2))


def demo():
    # overlay: a 200x100 canvas with two boxes gets two numbered marks
    img = Image.new("RGB", (200, 100), (240, 240, 240))
    boxes = [{"x": 10, "y": 10, "w": 40, "h": 20, "id": 0},
             {"x": 80, "y": 40, "w": 50, "h": 30, "id": 7, "label": "Search"}]
    marked = draw_marks(img, boxes)
    assert marked.size == (200, 100)
    # the mark color is painted at the box origin
    assert marked.getpixel((10, 10))[0] > 200 and marked.getpixel((10, 10))[1] < 40

    els = to_elements(boxes)
    assert els[0]["id"] == 0 and els[0]["x"] == 30 and els[0]["y"] == 20
    assert els[1]["id"] == 7 and els[1]["label"] == "Search"
    assert els[1]["bbox"] == [80, 40, 130, 70]

    assert parse_som_id("click [12] the search box") == 12
    assert parse_som_id("id=3") == 3
    assert parse_som_id("no numbers here at all") is None

    # detector: a dark rectangle on white is a region
    canvas = Image.new("RGB", (160, 80), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    d.rectangle([20, 15, 80, 45], outline=(0, 0, 0), width=3)
    d.rectangle([100, 20, 145, 55], outline=(0, 0, 0), width=3)
    found = detect_ui_boxes(canvas, min_size=8)
    assert len(found) >= 1, found
    # iou of two identical boxes is 1
    assert abs(_iou(boxes[0], boxes[0]) - 1.0) < 1e-9
    assert _iou(boxes[0], boxes[1]) == 0.0
    print("ok")


if __name__ == "__main__":
    demo()
