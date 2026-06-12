"""
Central config for the screen agent.

All the tunable knobs live here so you're not hunting through multiple files
when you want to change something simple like the output directory or which
monitor to capture.
"""

from pathlib import Path
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    # where Ollama is listening
    ollama_host: str = "http://localhost:11434"

    # the vision model to use - needs to be a multimodal model that understands images
    ollama_model: str = "llama3.2-vision:latest"

    # how long (in seconds) Ollama keeps the model loaded between calls.
    # set to 0 to unload immediately after each call, which frees up memory
    # but makes the next call slower. 300 (5 minutes) is a decent middle ground.
    ollama_keep_alive: int = 300

    # which monitor to capture. 1 is your primary display.
    # if you have multiple screens, try 2 or 3.
    screenshot_monitor: int = 1

    # on Retina/HiDPI displays, mss captures at 2x resolution.
    # OmniParser expects regular-res images, so we scale down.
    # set to 1.0 if you're on a non-HiDPI display.
    display_scale_factor: float = 2.0

    # where annotated screenshots and element JSONs get saved
    output_dir: Path = Path("outputs")

    # where OmniParser weights live
    weights_dir: Path = Path("weights")

    # OmniParser detection threshold - higher means fewer, more confident boxes.
    # lower means more boxes but also more false positives.
    detection_threshold: float = 0.05

    # max elements OmniParser will label before truncating.
    # 50 is usually plenty for a normal screen.
    max_elements: int = 50

    # seconds between agent cycles in the main loop
    loop_delay: float = 2.0

    # how many cycles to run before stopping. set to None to run forever.
    max_cycles: int | None = None

    # MCP server settings
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765

    class Config:
        env_prefix = "SCREEN_AGENT_"
        env_file = ".env"


# module-level singleton so you just do `from config import cfg`
cfg = Config()

# make sure output and weights directories exist
cfg.output_dir.mkdir(exist_ok=True)
cfg.weights_dir.mkdir(exist_ok=True)
(cfg.weights_dir / "icon_detect").mkdir(exist_ok=True)
(cfg.weights_dir / "icon_caption_florence").mkdir(exist_ok=True)
