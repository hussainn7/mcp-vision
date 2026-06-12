"""
Downloads OmniParser v2 weights using the huggingface_hub Python API.
Run this once, then you're offline for everything except Ollama model pulls.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
WEIGHTS_DIR = ROOT / "weights"

try:
    from huggingface_hub import hf_hub_download
except ImportError:
    print("huggingface_hub not found. Activate your venv first.")
    sys.exit(1)


FILES = [
    "icon_detect/train_args.yaml",
    "icon_detect/model.pt",
    "icon_detect/model.yaml",
    "icon_caption/config.json",
    "icon_caption/generation_config.json",
    "icon_caption/model.safetensors",
]

REPO = "microsoft/OmniParser-v2.0"


def main():
    print(f"Downloading OmniParser v2 weights -> {WEIGHTS_DIR.resolve()}")
    print("This is a one-time download (~2GB). Needs internet access.\n")

    WEIGHTS_DIR.mkdir(exist_ok=True)

    for file_path in FILES:
        subfolder, filename = file_path.rsplit("/", 1)
        dest_dir = WEIGHTS_DIR / subfolder
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest_file = dest_dir / filename
        if dest_file.exists():
            print(f"  [skip] {file_path} (already downloaded)")
            continue

        print(f"  Fetching {file_path}...")
        hf_hub_download(
            repo_id=REPO,
            filename=file_path,
            local_dir=str(WEIGHTS_DIR),
        )
        print(f"  [ok]   {file_path}")

    # OmniParser expects icon_caption_florence, not icon_caption
    icon_caption = WEIGHTS_DIR / "icon_caption"
    icon_caption_florence = WEIGHTS_DIR / "icon_caption_florence"

    if icon_caption.exists() and not icon_caption_florence.exists():
        icon_caption.rename(icon_caption_florence)
        print("\nRenamed weights/icon_caption -> weights/icon_caption_florence")
    elif icon_caption_florence.exists():
        print("\nweights/icon_caption_florence already in place.")

    print(f"\nDone. Run `python scripts/check_setup.py` to verify.")


if __name__ == "__main__":
    main()
