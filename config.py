"""
Central config. Override any of these with SCREEN_AGENT_* env vars or a .env file.
"""

from pathlib import Path
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    # where Ollama is listening
    ollama_host: str = "http://localhost:11434"

    # vision model for simple_agent.py — must be a grounding VLM that outputs
    # pixel coordinates. qwen2.5vl is the smallest that reliably lands clicks.
    model: str = "qwen2.5vl:7b"

    # text model for mac_agent.py — picks tools and fills arguments. qwen3 is
    # good at function calling and runs fast on Apple Silicon (Metal).
    planning_model: str = "qwen3:8b"

    # how long Ollama keeps the model in memory between calls, in seconds.
    # keep this high — reloading 7B of weights costs several seconds.
    ollama_keep_alive: int = 600

    # which monitor to capture. 1 is your primary display.
    screenshot_monitor: int = 1

    # Leave at 1.0. The agent derives the pixel->click scale from the real
    # screen width at runtime, so pre-shrinking here only throws away detail.
    # This knob stays because mss on some multi-monitor setups reports a
    # resolution that needs correcting by hand.
    display_scale_factor: float = 1.0

    # screenshots are downscaled to this width before inference. Lower = faster,
    # but grounding gets sloppier on dense UIs. This is the accuracy/speed knob.
    inference_width: int = 1280

    # where screenshots get saved
    output_dir: Path = Path("outputs")

    # where run trajectories (JSONL) get saved; view with trace_viewer.py
    trace_dir: Path = Path("traces")

    # seconds to wait after each action before looking again
    loop_delay: float = 0.5

    # give up after this many actions
    max_steps: int = 15

    # MCP server settings
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765

    # Playwright CDP endpoint for attaching to Chrome launched with
    # --remote-debugging-port=9222. Only used by the MCP server.
    playwright_cdp_endpoint: str = "http://localhost:9222"

    class Config:
        env_prefix = "SCREEN_AGENT_"
        env_file = ".env"


cfg = Config()
cfg.output_dir.mkdir(exist_ok=True)
