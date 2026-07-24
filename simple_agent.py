"""
Screenshot -> VLM -> mouse. No OmniParser, no AX tree, no element IDs.

The model looks at the raw screen and returns pixel coordinates, like Claude's
computer-use loop. Needs a grounding-capable VLM:  ollama pull qwen2.5vl:7b

    python simple_agent.py "search google for the weather in dubai"
"""

import json
import sys
import time

import ollama
import pyautogui
from PIL import Image

from config import cfg
from phase1_vision.capture import capture_screen

MODEL = "qwen2.5vl:7b"
MAX_W = 1280  # downscale before inference; coords scale back linearly

SYSTEM = """You control a macOS desktop. You see a screenshot. Output ONE action.

Coordinates are pixels in the image you were given, origin top-left.

- click / double_click / right_click: set x and y
- type: set text (types at the current cursor)
- key: set text to a shortcut like "enter", "cmd+space", "cmd+l"
- scroll: set x, y and amount (negative scrolls down)
- done: set text to the answer or a summary of what you accomplished

If the app you need isn't visible, use key "cmd+space" and type its name."""

SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "action": {
            "type": "string",
            "enum": ["click", "double_click", "right_click", "type", "key", "scroll", "done"],
        },
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "amount": {"type": "integer"},
        "text": {"type": "string"},
    },
    "required": ["reason", "action"],
}


def shrink(img: Image.Image) -> tuple[Image.Image, float]:
    """Downscale for inference. Returns (image, factor to multiply coords by)."""
    if img.width <= MAX_W:
        return img, 1.0
    scale = MAX_W / img.width
    return img.resize((MAX_W, int(img.height * scale)), Image.LANCZOS), 1 / scale


def act(a: dict, scale: float) -> str:
    """Execute one action dict. Returns a short result string."""
    kind = a["action"]
    x, y = int(a.get("x", 0) * scale), int(a.get("y", 0) * scale)

    if kind == "click":
        pyautogui.click(x, y)
    elif kind == "double_click":
        pyautogui.doubleClick(x, y)
    elif kind == "right_click":
        pyautogui.rightClick(x, y)
    elif kind == "type":
        pyautogui.write(a.get("text", ""), interval=0.01)
    elif kind == "key":
        pyautogui.hotkey(*a.get("text", "").split("+"))
    elif kind == "scroll":
        pyautogui.scroll(a.get("amount", -5), x=x, y=y)
    else:
        return "done"
    return f"{kind}({x},{y}) {a.get('text', '')}".strip()


def run(task: str, max_steps: int = 15) -> str:
    history: list[str] = []

    for step in range(max_steps):
        img, _ = capture_screen(save=False)
        img, scale = shrink(img)

        prompt = f"Task: {task}\n\nDone so far:\n" + (
            "\n".join(f"{i + 1}. {h}" for i, h in enumerate(history)) or "  (nothing yet)"
        )
        buf = cfg.output_dir / "_frame.png"
        img.save(buf)

        reply = ollama.chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt, "images": [str(buf)]},
            ],
            format=SCHEMA,
            keep_alive=cfg.ollama_keep_alive,
            options={"temperature": 0, "num_predict": 200},
        )
        a = json.loads(reply["message"]["content"])
        print(f"[{step + 1}] {a['action']}: {a['reason']}")

        if a["action"] == "done":
            return a.get("text", "done")

        history.append(act(a, scale))
        time.sleep(cfg.loop_delay)

    return "hit max steps"


def demo():
    # ponytail: only the coordinate math is worth a check; the rest is I/O.
    img, scale = shrink(Image.new("RGB", (2560, 1440)))
    assert img.width == 1280 and abs(scale - 2.0) < 1e-9
    small, s = shrink(Image.new("RGB", (800, 600)))
    assert small.width == 800 and s == 1.0
    assert act({"action": "done", "text": "x"}, 1.0) == "done"
    print("ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
    else:
        print(run(" ".join(sys.argv[1:]) or "open Safari"))
