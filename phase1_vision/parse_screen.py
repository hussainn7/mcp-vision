"""
OmniParser integration.

We clone OmniParser into vendor/omniparser and drive its util.utils functions
directly. This keeps us on the real implementation rather than a reimplementation,
and makes it easy to pull upstream fixes by just updating the submodule.

Memory: models are loaded, run, then explicitly deleted before we return.
The orchestrator calls Ollama right after, so we need that memory back.
"""

import base64
import gc
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from PIL import Image
from loguru import logger

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

OMNIPARSER_DIR = ROOT / "vendor" / "omniparser"
if not OMNIPARSER_DIR.exists():
    raise RuntimeError(
        f"OmniParser not found at {OMNIPARSER_DIR}.\n"
        f"Run: git clone https://github.com/microsoft/OmniParser vendor/omniparser"
    )
sys.path.insert(0, str(OMNIPARSER_DIR))

from util.utils import get_caption_model_processor, get_som_labeled_img, get_yolo_model

from config import cfg


def _pick_device() -> str:
    """Best available device. MPS on Apple Silicon, then CUDA, then CPU."""
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def parse_screen(image: Image.Image) -> tuple[Image.Image, list[dict[str, Any]]]:
    """
    Run the full OmniParser pipeline on a PIL Image.

    Loads YOLO + Florence-2, runs detection and captioning, then frees both
    models from memory before returning. The caller should call Ollama after.

    Args:
        image: PIL Image at normal (1x, non-Retina) resolution

    Returns:
        annotated_image: the screenshot with numbered bounding boxes
        elements: list of dicts - id, label, x, y, box, interactivity
    """
    yolo_device = _pick_device()
    logger.info(f"OmniParser: YOLO on {yolo_device} (caption pass disabled)")
    logger.info(f"Image size: {image.size[0]}x{image.size[1]} px")

    detect_path = str(cfg.weights_dir / "icon_detect" / "model.pt")
    caption_path = str(cfg.weights_dir / "icon_caption_florence")

    if not Path(detect_path).exists():
        raise FileNotFoundError(
            f"Detection weights missing: {detect_path}\n"
            f"Run: python scripts/download_weights.py"
        )

    # load YOLO only - no caption model
    yolo_model = get_yolo_model(model_path=detect_path)

    # get_som_labeled_img returns: (base64_png_str, label_coordinates_dict, filtered_boxes_elem_list)
    #   label_coordinates: {"0": [cx, cy, w, h] normalized}
    #   filtered_boxes_elem: [{"type", "bbox": [x1,y1,x2,y2] normalized, "content", ...}]
    draw_bbox_config = {
        "text_scale": 0.8,
        "text_thickness": 2,
        "text_padding": 3,
        "thickness": 2,
    }

    # use_local_semantics=False skips Florence-2 entirely.
    # YOLO-only takes ~600ms vs 4+ minutes for CPU captioning.
    # The orchestrator's vision LLM sees the full annotated image anyway.
    annotated_b64, label_coordinates, filtered_boxes_elem = get_som_labeled_img(
        image,
        yolo_model,
        BOX_TRESHOLD=cfg.detection_threshold,
        output_coord_in_ratio=True,
        ocr_bbox=None,
        draw_bbox_config=draw_bbox_config,
        caption_model_processor=None,
        ocr_text=[],
        use_local_semantics=False,
        iou_threshold=0.1,
        imgsz=640,
    )

    del yolo_model
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    logger.debug("YOLO model freed")

    # decode the annotated image
    annotated_image = Image.open(io.BytesIO(base64.b64decode(annotated_b64)))

    # build the element list from label_coordinates + filtered_boxes_elem
    # label_coordinates keys are stringified ints matching the box label drawn on the image.
    # filtered_boxes_elem has the content/caption for each box.
    w, h = image.size
    elements: list[dict] = []

    for i, box_elem in enumerate(filtered_boxes_elem):
        key = str(i)
        label = box_elem.get("content") or "unknown"
        interactivity = box_elem.get("interactivity", True)

        # coords come back normalized (0-1), convert to pixel space
        norm_box = box_elem.get("bbox", [0, 0, 0, 0])
        x1 = int(norm_box[0] * w)
        y1 = int(norm_box[1] * h)
        x2 = int(norm_box[2] * w)
        y2 = int(norm_box[3] * h)
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        # label_coordinates gives us the center+wh in normalized form too
        if key in label_coordinates:
            nc = label_coordinates[key]
            cx = int(nc[0] * w)
            cy = int(nc[1] * h)

        elements.append({
            "id": i + 1,
            "label": str(label).strip(),
            "x": cx,
            "y": cy,
            "box": [x1, y1, x2, y2],
            "interactivity": interactivity,
        })

    # cap to configured max
    elements = elements[: cfg.max_elements]
    logger.info(f"Parsed {len(elements)} elements")
    return annotated_image, elements


def save_results(
    annotated_image: Image.Image,
    elements: list[dict],
    timestamp: str | None = None,
) -> tuple[Path, Path]:
    """
    Save annotated screenshot and element JSON to cfg.output_dir.

    Returns: (image_path, json_path)
    """
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    image_path = cfg.output_dir / f"annotated_{timestamp}.png"
    json_path = cfg.output_dir / f"elements_{timestamp}.json"

    annotated_image.save(image_path)
    with open(json_path, "w") as f:
        json.dump(elements, f, indent=2)

    logger.info(f"Saved: {image_path.name}  +  {json_path.name}")
    return image_path, json_path


if __name__ == "__main__":
    # Phase 1 smoke test. No LLM involved.
    from phase1_vision.capture import capture_screen

    print("Capturing screen...")
    img, _ = capture_screen(save=False)

    print("Running OmniParser (first run downloads easyocr models, ~30s)...")
    annotated, elements = parse_screen(img)

    print(f"\nFound {len(elements)} elements:")
    for elem in elements:
        flag = "  " if elem["interactivity"] else "* "
        print(f"  {flag}[{elem['id']:2d}] ({elem['x']:4d}, {elem['y']:4d})  {elem['label']}")

    img_path, json_path = save_results(annotated, elements)
    print(f"\nAnnotated screenshot -> {img_path}")
    print(f"Element JSON        -> {json_path}")
    print("\nOpen the annotated PNG to verify the numbered bounding boxes.")
