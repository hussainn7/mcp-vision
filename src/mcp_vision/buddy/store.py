"""Small on-disk state: user preferences and conversation history."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mcp_vision.paths import state_dir



def config_dir() -> Path:
    return Path(os.environ.get("MCP_VISION_CONFIG_DIR", Path.home() / ".config" / "mcp-vision"))


@dataclass
class Prefs:
    engine: str = ""                 # engine id; "" = best available
    depth: str = "balanced"          # fast | balanced | deep
    walkthroughs: bool = True
    sounds: bool = True              # notch UI sounds (buddy/sounds.py)
    buddy: bool = True               # show Plip by the cursor while idle
    companion: str = "notch"         # notch: Plip lives in the notch and drips out to point | cursor | hidden
    tts: str = ""                    # "" = follow settings/env
    stt: str = ""
    onboarded: bool = False
    tour_step: str = ""              # welcome tour step, resumed after a restart
    update_check: bool = True        # once a day, ask GitHub whether a newer Plip is out
    hotkey: str = "control+option"   # hold to talk: one of hotkey.CHORDS
    tasks_done: int = 0              # finished tasks, ever (the 1st and 3rd are when Plip asks for Google)
    signin_asks: int = 0             # times they tapped Later on the Google card; after 3, Plip stops asking
    asked_on_update: bool = False    # the "a newer Plip is out" moment was used
    milestones: list[str] = field(default_factory=list)   # funnel events sent once per install (analytics)

    @classmethod
    def load(cls, path: Path | None = None) -> Prefs:
        path = path or config_dir() / "prefs.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        known = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: Path | None = None) -> None:
        path = path or config_dir() / "prefs.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))


@dataclass
class History:
    path: Path = field(default_factory=lambda: state_dir() / "history.jsonl")
    limit: int = 200

    def add(self, question: str, answer: str, engine: str = "") -> None:
        if not question.strip() or not answer.strip():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"question": question, "answer": answer, "engine": engine,
                                     "at": int(time.time())}) + "\n")
        items = self.items()
        if len(items) > self.limit * 2:
            self._rewrite(items[-self.limit:])

    def items(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out[-self.limit:]

    def clear(self) -> None:
        self._rewrite([])

    def _rewrite(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
