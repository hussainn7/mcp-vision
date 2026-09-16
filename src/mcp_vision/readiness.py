"""Check the selected model before spending a task's browser/action budget."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen


def ensure_model_ready(provider: str) -> None:
    from config import cfg
    if provider != 'local':
        if not getattr(cfg, f'{provider}_api_key', None):
            raise RuntimeError(f'{provider.capitalize()} is not configured. Choose Local or configure its API key.')
        return
    host = cfg.ollama_host.rstrip('/')
    def models():
        with urlopen(host + '/api/tags', timeout=3) as response:
            return json.load(response).get('models', [])
    try:
        available = models()
    except Exception as exc:
        # Start an installed local service; never change remote endpoints or silently
        # install software, download multi-GB weights, or switch to a cloud provider.
        local = urlsplit(host).hostname in {'localhost', '127.0.0.1', '::1'}
        if sys.platform == 'darwin' and local and Path('/Applications/Ollama.app').exists():
            subprocess.run(['open', '-g', '-a', 'Ollama'], capture_output=True, timeout=10, check=False)
            try:
                available = models()
            except Exception:
                raise RuntimeError('Ollama is starting. Try Go again in a moment. If it does not start, open Ollama from Applications.') from exc
        else:
            raise RuntimeError('The local model service is unavailable. Start Ollama, or choose a configured provider.') from exc
    names = {m.get('name', '') for m in available}
    configured = cfg.planning_model
    if configured not in names and configured + ':latest' not in names:
        raise RuntimeError(f'The local model {configured} is not installed. Install it in Ollama or set SCREEN_AGENT_PLANNING_MODEL to an installed model.')
