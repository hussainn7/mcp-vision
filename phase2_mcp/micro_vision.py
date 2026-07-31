"""
Micro-vision: the last resort when a structured click can't be made.

The usual vision fallback sends a full screenshot to a VLM and asks it to find
a button. That is slow (a 1512px frame is a lot of tokens), and a small local
model is bad at it — the whole reason this project moved off pixel-guessing.

Micro-vision keeps the vision step but shrinks the problem: crop a small box
around where the element already is (the browser told us), and ask the VLM for
one coordinate inside that crop. A ~256px image is fast on a local model, and
"point at the button in this thumbnail" is a much easier question than "find
the button on this desktop".

The coordinate mapping is pure arithmetic and unit-tested; only the model call
needs a browser or Ollama.
"""

import json

from loguru import logger

VISION_SYSTEM = (
    "You are given a small cropped screenshot of part of a web page. "
    "Reply with JSON {\"x\": <int>, \"y\": <int>} giving the pixel coordinates, "
    "within this cropped image, of the centre of the described element. "
    "If it is not in the image, reply {\"x\": -1, \"y\": -1}."
)

_POINT_SCHEMA = {
    "type": "object",
    "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
    "required": ["x", "y"],
}


def to_viewport(px, py, image_size, clip):
    """Map a point in the cropped image back to viewport coordinates.

    The crop is taken at some device scale, so the PNG is usually larger than
    the CSS box it came from; scale by the real ratio rather than assuming 1:1.
    """
    img_w, img_h = image_size
    if not img_w or not img_h:
        return None
    sx = clip["width"] / img_w
    sy = clip["height"] / img_h
    return (clip["x"] + px * sx, clip["y"] + py * sy)


def in_bounds(px, py, image_size):
    img_w, img_h = image_size
    return 0 <= px < img_w and 0 <= py < img_h


def locate(png_bytes, description, model, keep_alive=600):
    """Ask a small VLM for the element's coordinates inside the crop.

    Returns (x, y) in *image* pixels, or None if the model can't see it.
    Import-time-safe: ollama/PIL are only touched when this actually runs.
    """
    import io

    import ollama
    from PIL import Image

    try:
        size = Image.open(io.BytesIO(png_bytes)).size
    except Exception as e:
        logger.debug(f"micro-vision: unreadable crop: {e}")
        return None, None

    try:
        reply = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": VISION_SYSTEM},
                {"role": "user", "content": f"Element: {description}", "images": [png_bytes]},
            ],
            format=_POINT_SCHEMA,
            keep_alive=keep_alive,
            options={"temperature": 0, "num_predict": 40},
        )
        point = json.loads(reply["message"]["content"])
    except Exception as e:
        logger.debug(f"micro-vision: model call failed: {e}")
        return None, size

    px, py = int(point.get("x", -1)), int(point.get("y", -1))
    if px < 0 or py < 0 or not in_bounds(px, py, size):
        logger.debug(f"micro-vision: model reported no/out-of-range point {px},{py} for {size}")
        return None, size
    return (px, py), size


def demo():
    clip = {"x": 100, "y": 200, "width": 200, "height": 100}

    # Retina crop: PNG is 2x the CSS box, so a point maps back at half scale.
    assert to_viewport(0, 0, (400, 200), clip) == (100, 200)
    assert to_viewport(400, 200, (400, 200), clip) == (300, 300)     # bottom-right corner
    assert to_viewport(200, 100, (400, 200), clip) == (200, 250)     # centre

    # 1:1 crop maps straight through, offset by the clip origin.
    assert to_viewport(10, 10, (200, 100), clip) == (110, 210)

    # Degenerate image size never divides by zero.
    assert to_viewport(1, 1, (0, 0), clip) is None

    assert in_bounds(0, 0, (10, 10)) and in_bounds(9, 9, (10, 10))
    assert not in_bounds(10, 10, (10, 10)) and not in_bounds(-1, 5, (10, 10))
    print("ok")


if __name__ == "__main__":
    demo()
