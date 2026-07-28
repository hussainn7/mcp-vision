"""
Screenshot -> VLM -> mouse. No OmniParser, no AX tree, no element IDs.

The model looks at the raw screen and returns pixel coordinates, like Claude's
computer-use loop. Needs a grounding-capable VLM:  ollama pull qwen2.5vl:7b

    python simple_agent.py "search google for the weather in dubai"
"""

import json
import subprocess
import sys
import time

import ollama
import pyautogui
from PIL import Image

from config import cfg
from phase1_vision.capture import capture_screen

MODEL = cfg.model
MAX_W = cfg.inference_width  # downscale before inference; coords scale back linearly

SYSTEM = """You control a macOS desktop. You see a screenshot. Output ONE action.

Coordinates are pixels in the image you were given, origin top-left.

- click / double_click / right_click: set x and y
- type: set text (types at the current cursor)
- key: set text to a shortcut like "enter", "cmd+space", "cmd+l"
- scroll: set x, y and amount (negative scrolls down)
- open_app: set text to an app name (e.g. "Safari") to launch or focus it
- done: set text to the answer or a summary of what you accomplished

To use an app that isn't already visible, open_app it. Do NOT open Spotlight
or click things you can't see. Only interact with elements visible right now."""

SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "action": {
            "type": "string",
            "enum": ["click", "double_click", "right_click", "type", "key", "scroll", "open_app", "done"],
        },
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "amount": {"type": "integer"},
        "text": {"type": "string"},
    },
    "required": ["reason", "action"],
}


def shrink(img: Image.Image, screen_w: int) -> tuple[Image.Image, float]:
    """
    Downscale for inference and return the factor mapping image pixels back to
    the logical points pyautogui clicks in.

    Deriving the factor from the real screen width rather than a hardcoded
    Retina constant means this is correct whether mss hands back physical
    pixels (3024 wide) or logical ones (1512).
    """
    if img.width > MAX_W:
        img = img.resize((MAX_W, int(img.height * MAX_W / img.width)), Image.LANCZOS)
    return img, screen_w / img.width


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
    elif kind == "open_app":
        # native launch/focus — deterministic, no Spotlight grounding needed
        subprocess.run(["open", "-a", a.get("text", "")], check=False)
        time.sleep(1.5)  # let the window come up before the next screenshot
        return f"open_app {a.get('text', '')}"
    else:
        return "done"
    return f"{kind}({x},{y}) {a.get('text', '')}".strip()


def run(task: str, max_steps: int | None = None) -> str:
    max_steps = max_steps or cfg.max_steps
    history: list[str] = []

    for step in range(max_steps):
        img, _ = capture_screen(save=False)
        img, scale = shrink(img, pyautogui.size()[0])

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
    # Retina: 3024px grab shown at 1512 logical -> click coords must double.
    img, scale = shrink(Image.new("RGB", (3024, 1964)), screen_w=1512)
    assert img.width == 1280 and abs(scale - 1512 / 1280) < 1e-9
    # Non-Retina: grab already matches the screen, no scaling at all.
    img, scale = shrink(Image.new("RGB", (1280, 800)), screen_w=1280)
    assert img.width == 1280 and scale == 1.0
    # mss handing back logical points on a HiDPI Mac (what this machine does).
    img, scale = shrink(Image.new("RGB", (1512, 982)), screen_w=1512)
    assert abs(scale - 1512 / 1280) < 1e-9
    assert act({"action": "done", "text": "x"}, 1.0) == "done"
    print("ok")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--demo":
        return demo()
    steps = None
    if "--steps" in args:
        i = args.index("--steps")
        steps = int(args[i + 1])
        args = args[:i] + args[i + 2 :]
    if not args:
        print(__doc__)
        return
    print("\n=== RESULT ===")
    print(run(" ".join(args), steps))


if __name__ == "__main__":
    main()
