"""Numbered bounding boxes + optional OCR. Pure Pillow when tesseract is missing."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from mcp_vision.core.models import BoundingBox, ScreenElement, ScreenInspectionResult
from mcp_vision.log import get_logger

log = get_logger("mcp_vision.parser")


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    if hasattr(draw, "textbbox"):
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        return r - l, b - t
    return (len(text) * 6, 10)


def draw_marks(img: Image.Image, elements: list[ScreenElement]) -> Image.Image:
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("Arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    for el in elements:
        x, y, w, h = el.bbox.x, el.bbox.y, el.bbox.w, el.bbox.h
        draw.rectangle([x, y, x + w, y + h], outline=(255, 0, 80), width=2)
        label = str(el.id)
        tw, th = _text_size(draw, label, font)
        by1 = max(0, y - th - 4)
        draw.rectangle([x, by1, x + tw + 4, by1 + th + 4], fill=(255, 0, 80))
        draw.text((x + 2, by1 + 2), label, fill=(255, 255, 255), font=font)
    return out


def _iou(a: dict[str, int], b: dict[str, int]) -> float:
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union else 0.0


def detect_boxes(
    img: Image.Image,
    max_boxes: int = 40,
    min_size: int = 12,
    max_frac: float = 0.6,
) -> list[dict[str, int]]:
    """Dark blobs + contrast edges -> button-like rectangles."""
    from PIL import ImageChops

    rgb = img.convert("RGB")
    w, h = rgb.size
    gray = rgb.convert("L")
    dark = gray.point(lambda p: 255 if p < 80 else 0)
    edges = gray.filter(ImageFilter.FIND_EDGES).point(lambda p: 255 if p > 40 else 0)
    bw = ImageChops.lighter(dark, edges)
    px = bw.load()
    visited = bytearray(w * h)
    boxes: list[dict[str, int]] = []
    max_w, max_h = int(w * max_frac), int(h * max_frac)

    def idx(x: int, y: int) -> int:
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
                minx, maxx = min(minx, cx), max(maxx, cx)
                miny, maxy = min(miny, cy), max(maxy, cy)
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
            boxes.append({"x": minx, "y": miny, "w": bw_, "h": bh_})

    boxes.sort(key=lambda b: (b["y"] // 20, b["x"]))
    kept: list[dict[str, int]] = []
    for b in boxes:
        if any(_iou(b, k) > 0.7 for k in kept):
            continue
        kept.append(b)
        if len(kept) >= max_boxes:
            break
    return kept


def _ocr_crop(img: Image.Image, box: dict[str, int]) -> str:
    try:
        import pytesseract
    except ImportError:
        return ""
    crop = img.crop((box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]))
    try:
        text = pytesseract.image_to_string(crop, config="--psm 7").strip()
    except Exception as e:
        log.debug("ocr failed: %s", e)
        return ""
    return " ".join(text.split())[:80]


def parse_elements(img: Image.Image, ocr: bool = True) -> list[ScreenElement]:
    boxes = detect_boxes(img)
    out: list[ScreenElement] = []
    for i, b in enumerate(boxes):
        text = _ocr_crop(img, b) if ocr else ""
        bbox = BoundingBox(x=b["x"], y=b["y"], w=b["w"], h=b["h"])
        label = text or f"element {i}"
        out.append(ScreenElement(
            id=i, label=label, role="visual", bbox=bbox,
            cx=bbox.cx, cy=bbox.cy, text=text,
        ))
    return out


def inspect_image(
    img: Image.Image,
    display_id: int = 0,
    scale: float = 1.0,
    png: bytes = b"",
    ocr: bool = True,
) -> ScreenInspectionResult:
    elements = parse_elements(img, ocr=ocr)
    return ScreenInspectionResult(
        display_id=display_id,
        width=img.width,
        height=img.height,
        scale=scale,
        elements=elements,
        png=png,
        source="visual",
    )
