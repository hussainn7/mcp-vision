"""
Screen capture using mss.

mss is fast and lightweight - it uses native OS APIs instead of going through
something heavyweight like Pillow's ImageGrab. On macOS that means it talks
directly to the Quartz compositor, so you get a clean, accurate frame.
"""

import sys
from datetime import datetime
from pathlib import Path

import mss
import mss.tools
from PIL import Image
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import cfg


def capture_screen(monitor_index: int | None = None, save: bool = True) -> tuple[Image.Image, Path | None]:
    """
    Take a screenshot of the specified monitor and return it as a PIL Image.

    Args:
        monitor_index: which display to capture. defaults to cfg.screenshot_monitor.
        save: whether to save a raw (un-annotated) copy to the output dir.

    Returns:
        A tuple of (PIL Image, path to saved file or None).
    """
    monitor_index = monitor_index or cfg.screenshot_monitor

    with mss.mss() as sct:
        monitors = sct.monitors
        if monitor_index >= len(monitors):
            logger.warning(
                f"Monitor {monitor_index} doesn't exist (only {len(monitors) - 1} found). "
                f"Falling back to primary."
            )
            monitor_index = 1

        monitor = monitors[monitor_index]
        logger.debug(f"Capturing monitor {monitor_index}: {monitor}")

        raw = sct.grab(monitor)
        # mss returns BGRA, PIL wants RGBA - swap the channels
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    # Retina displays capture at double resolution, scale down to 1x
    # so OmniParser doesn't choke on a 5120x2880 image
    if cfg.display_scale_factor != 1.0:
        new_w = int(img.width / cfg.display_scale_factor)
        new_h = int(img.height / cfg.display_scale_factor)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        logger.debug(f"Scaled down to {new_w}x{new_h}")

    saved_path = None
    if save:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_path = cfg.output_dir / f"raw_{timestamp}.png"
        img.save(saved_path)
        logger.info(f"Raw screenshot saved: {saved_path}")

    return img, saved_path


if __name__ == "__main__":
    # quick test - run this file directly to grab a screenshot and see what you get
    img, path = capture_screen()
    print(f"Captured {img.size[0]}x{img.size[1]} image -> {path}")
