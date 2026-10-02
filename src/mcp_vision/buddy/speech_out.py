"""Text to speech for the buddy.

``QueueSpeaker`` pipelines sentences: while one sentence plays, the next is
already being synthesized, so a streamed reply sounds continuous. ``stop``
cuts playback immediately (push-to-talk interrupts the buddy).
"""
from __future__ import annotations

import asyncio
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class Voice(Protocol):
    name: str

    def prepare(self, text: str) -> Any: ...                 # may hit the network
    def play(self, prepared: Any, stop: threading.Event) -> None: ...   # blocks until done/stopped


def _run_until_stopped(command: list[str], stop: threading.Event) -> None:
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        while process.poll() is None:
            if stop.wait(0.03):
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                return
    finally:
        if process.poll() is None:
            process.kill()


class SayVoice:
    """macOS built-in speech: zero setup, works offline."""

    name = "say"

    def __init__(self, voice: str | None = None, rate: int = 200, runner=_run_until_stopped):
        self.voice = voice
        self.rate = rate
        self.runner = runner

    def prepare(self, text: str) -> str:
        return text

    def play(self, prepared: str, stop: threading.Event) -> None:
        command = ["say", "-r", str(self.rate)]
        if self.voice:
            command += ["-v", self.voice]
        self.runner([*command, "--", prepared], stop)


class EspeakVoice(SayVoice):
    name = "espeak"

    def play(self, prepared: str, stop: threading.Event) -> None:
        self.runner(["espeak", "-s", str(min(self.rate, 260)), "--", prepared], stop)


class PrintVoice:
    """Headless fallback: shows what would be spoken."""

    name = "print"

    def __init__(self, write: Callable[[str], None] | None = None):
        self.write = write or (lambda text: print(f"buddy: {text}", flush=True))

    def prepare(self, text: str) -> str:
        return text

    def play(self, prepared: str, stop: threading.Event) -> None:
        if not stop.is_set():
            self.write(prepared)


@dataclass(frozen=True)
class ElevenLabsAudio:
    data: bytes
    suffix: str = ".mp3"


class ElevenLabsVoice:
    """Natural voice via ElevenLabs' streaming text-to-speech endpoint."""

    name = "elevenlabs"
    BASE = "https://api.elevenlabs.io/v1/text-to-speech"

    def __init__(self, *, api_key: str, voice_id: str, model_id: str = "eleven_flash_v2_5",
                 output_format: str = "mp3_44100_128", timeout: float = 20.0,
                 opener: Callable[..., Any] = urllib.request.urlopen,
                 runner=_run_until_stopped, player: str | None = None):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.output_format = output_format
        self.timeout = timeout
        self.opener = opener
        self.runner = runner
        self.player = player or ("afplay" if sys.platform == "darwin" else
                                 shutil.which("ffplay") or shutil.which("mpg123") or "")

    def request(self, text: str) -> urllib.request.Request:
        query = urllib.parse.urlencode({"output_format": self.output_format})
        url = f"{self.BASE}/{urllib.parse.quote(self.voice_id)}/stream?{query}"
        body = {"text": text, "model_id": self.model_id,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
        import json

        return urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
            "xi-api-key": self.api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"})

    def prepare(self, text: str) -> ElevenLabsAudio:
        try:
            with self.opener(self.request(text), timeout=self.timeout) as response:
                return ElevenLabsAudio(response.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"ElevenLabs HTTP {exc.code}") from exc

    def play(self, prepared: ElevenLabsAudio, stop: threading.Event) -> None:
        if not prepared.data or not self.player or stop.is_set():
            return
        with tempfile.NamedTemporaryFile(suffix=prepared.suffix, delete=False) as handle:
            handle.write(prepared.data)
            path = handle.name
        try:
            name = os.path.basename(self.player)
            if name.startswith("ffplay"):
                command = [self.player, "-nodisp", "-autoexit", "-loglevel", "quiet", path]
            elif name.startswith("mpg123"):
                command = [self.player, "-q", path]
            else:
                command = [self.player, path]
            self.runner(command, stop)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


class QueueSpeaker:
    """Non-blocking speaker with one-sentence lookahead synthesis."""

    def __init__(self, voice: Voice, *, fallback: Voice | None = None, lookahead: int = 2):
        self.voice = voice
        self.fallback = fallback
        self._texts: queue.Queue = queue.Queue()
        self._ready: queue.Queue = queue.Queue(maxsize=max(1, lookahead))
        self._generation = 0
        self._stop = threading.Event()
        self._pending = 0
        self._lock = threading.Lock()
        self.errors: list[str] = []
        threading.Thread(target=self._prepare_loop, daemon=True, name="buddy-tts-prepare").start()
        threading.Thread(target=self._play_loop, daemon=True, name="buddy-tts-play").start()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._pending > 0

    def speak(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._lock:
            self._pending += 1
            generation = self._generation
        self._texts.put((generation, text))

    async def drain(self) -> None:
        while self.busy:
            await asyncio.sleep(0.03)

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            self._stop.set()
            self._pending = 0
        for pending in (self._texts, self._ready):
            try:
                while True:
                    pending.get_nowait()
            except queue.Empty:
                pass

    def _done(self, generation: int) -> None:
        with self._lock:
            if generation == self._generation and self._pending > 0:
                self._pending -= 1

    def _prepare_loop(self) -> None:
        while True:
            generation, text = self._texts.get()
            if generation != self._generation:
                continue
            voice, prepared = self.voice, None
            try:
                prepared = self.voice.prepare(text)
            except Exception as exc:
                self.errors.append(f"{self.voice.name}: {exc}")
                if self.fallback is not None:
                    voice = self.fallback
                    try:
                        prepared = voice.prepare(text)
                    except Exception as fallback_exc:
                        self.errors.append(f"{voice.name}: {fallback_exc}")
            if generation != self._generation:
                continue
            if prepared is None:
                self._done(generation)
                continue
            self._ready.put((generation, voice, prepared))

    def _play_loop(self) -> None:
        while True:
            generation, voice, prepared = self._ready.get()
            # Check and re-arm atomically with stop(): otherwise a stop landing
            # between the two would be wiped out and a stale sentence would play.
            with self._lock:
                if generation != self._generation:
                    continue
                self._stop.clear()
            try:
                voice.play(prepared, self._stop)
            except Exception as exc:
                self.errors.append(f"{voice.name}: {exc}")
            finally:
                self._done(generation)


def default_voice(settings: Any = None) -> tuple[Voice, Voice | None]:
    """Pick the best available voice and a local fallback for it."""
    local: Voice
    if sys.platform == "darwin" and shutil.which("say"):
        local = SayVoice(voice=getattr(settings, "say_voice", None) or None)
    elif shutil.which("espeak"):
        local = EspeakVoice()
    else:
        local = PrintVoice()
    choice = (getattr(settings, "tts", "auto") or "auto").lower()
    key = getattr(settings, "elevenlabs_api_key", None)
    if choice in {"auto", "elevenlabs"} and key:
        return ElevenLabsVoice(api_key=key, voice_id=settings.elevenlabs_voice_id,
                               model_id=settings.elevenlabs_model), local
    if choice == "print":
        return PrintVoice(), None
    return local, None
