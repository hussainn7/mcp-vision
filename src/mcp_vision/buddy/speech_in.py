"""Push-to-talk speech recognition for the buddy.

Two engines, one interface (``start`` on press, ``release`` on let-go,
``cancel`` when the chord turned out to be another shortcut):

* ``AssemblyAIListener`` - the engine Clicky ships: AssemblyAI streaming v3
  over a websocket, 16 kHz mono PCM. On release it sends ``ForceEndpoint``
  and delivers the transcript at the first finished turn or after a 1.4 s
  grace period, then ``Terminate``s the session.
* ``AppleListener`` - on-device Apple Speech; no key needed.

Callbacks may fire on any thread; the app hops them to the main thread.
"""
from __future__ import annotations

import array
import json
import math
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

ASSEMBLYAI_WS = "wss://streaming.assemblyai.com/v3/ws"
ASSEMBLYAI_TOKEN = "https://streaming.assemblyai.com/v3/token"
SAMPLE_RATE = 16_000
FINAL_GRACE = 1.4
KEYTERMS = ["Claude", "Anthropic", "OpenAI", "ChatGPT", "Cursor", "VS Code", "Xcode", "Figma",
            "Notion", "Slack", "GitHub", "localhost", "Next.js", "Python", "JavaScript"]


@dataclass
class ListenerCallbacks:
    partial: Callable[[str], None] = lambda text: None
    final: Callable[[str], None] = lambda text: None
    level: Callable[[float], None] = lambda level: None
    error: Callable[[str], None] = lambda message: None


class Listener(Protocol):
    name: str

    def start(self) -> None: ...
    def release(self) -> None: ...
    def cancel(self) -> None: ...


def pcm16_level(chunk: bytes) -> float:
    """RMS of little-endian int16 audio, scaled like Clicky's meter (x10.2, 0..1)."""
    if len(chunk) < 2:
        return 0.0
    samples = array.array("h")
    samples.frombytes(chunk[: len(chunk) // 2 * 2])
    step = max(1, len(samples) // 256)
    picked = samples[::step]
    rms = math.sqrt(sum(s * s for s in picked) / len(picked)) / 32768.0
    return min(1.0, rms * 10.2)


# -- AssemblyAI turn assembly (pure) ---------------------------------------------

@dataclass
class TurnAssembler:
    """Combine streamed turns: finished turns by order, plus the live partial."""

    turns: dict[int, tuple[str, bool]] = field(default_factory=dict)
    active_order: int | None = None
    active_text: str = ""

    def add(self, message: dict[str, Any]) -> tuple[str, bool]:
        """Feed a ``Turn`` message; returns (full transcript, turn finished)."""
        text = str(message.get("transcript") or "").strip()
        finished = bool(message.get("end_of_turn")) or bool(message.get("turn_is_formatted"))
        order = message.get("turn_order")
        if not isinstance(order, int):
            order = self.active_order if self.active_order is not None else max(self.turns, default=-1) + 1
        if finished:
            self.active_order, self.active_text = None, ""
            formatted = bool(message.get("turn_is_formatted"))
            existing = self.turns.get(order)
            if text and not (existing and existing[1] and not formatted):
                self.turns[order] = (text, formatted)
        else:
            self.active_order, self.active_text = order, text
        return self.text, finished

    @property
    def text(self) -> str:
        parts = [self.turns[key][0] for key in sorted(self.turns) if self.turns[key][0]]
        if self.active_text.strip():
            parts.append(self.active_text.strip())
        return " ".join(parts)


def assemblyai_url(token: str, keyterms: list[str] | None = None) -> str:
    query = {"sample_rate": SAMPLE_RATE, "encoding": "pcm_s16le", "format_turns": "true",
             "speech_model": "u3-rt-pro", "token": token}
    if keyterms:
        query["keyterms_prompt"] = json.dumps(sorted(set(keyterms), key=str.casefold))
    return f"{ASSEMBLYAI_WS}?{urllib.parse.urlencode(query)}"


def fetch_assemblyai_token(api_key: str, *, expires: int = 480,
                           opener: Callable[..., Any] = urllib.request.urlopen) -> str:
    request = urllib.request.Request(f"{ASSEMBLYAI_TOKEN}?expires_in_seconds={expires}",
                                     headers={"Authorization": api_key})
    with opener(request, timeout=5) as response:
        token = json.loads(response.read()).get("token")
    if not token:
        raise RuntimeError("AssemblyAI did not return a streaming token")
    return token


class AssemblyAISession:
    """One push-to-talk session over a websocket (transport injected for tests)."""

    def __init__(self, url: str, callbacks: ListenerCallbacks, *,
                 connect: Callable[[str], Any] | None = None, grace: float = FINAL_GRACE):
        self.url = url
        self.callbacks = callbacks
        self.grace = grace
        self._connect = connect or _websocket_connect
        self.assembler = TurnAssembler()
        self.ws = None
        self.ready = threading.Event()
        self._lock = threading.Lock()
        self._awaiting_final = False
        self._delivered = False
        self._closed = False
        self._timer: threading.Timer | None = None

    def open(self, timeout: float = 5.0) -> None:
        self.ws = self._connect(self.url)
        threading.Thread(target=self._receive_loop, daemon=True, name="buddy-assemblyai").start()
        if not self.ready.wait(timeout):
            self.cancel()                    # don't leave the socket and reader thread behind
            raise RuntimeError("AssemblyAI did not start the session")

    def send_audio(self, chunk: bytes) -> None:
        if self.ws is not None and not self._closed and self.ready.is_set():
            try:
                self.ws.send(chunk)
            except Exception as exc:
                self._fail(exc)

    def request_final(self) -> None:
        with self._lock:
            if self._delivered or self._awaiting_final:
                return
            self._awaiting_final = True
            self._timer = threading.Timer(self.grace, lambda: self._deliver(self.assembler.text))
            self._timer.daemon = True
            self._timer.start()
        self._send_json({"type": "ForceEndpoint"})

    def cancel(self) -> None:
        with self._lock:
            self._delivered = True
            if self._timer:
                self._timer.cancel()
        self._close()

    def _receive_loop(self) -> None:
        try:
            for raw in self.ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                self._handle(json.loads(raw))
                if self._closed:
                    break
        except Exception as exc:
            if not self._closed:
                self._fail(exc)
        finally:
            self.ready.set()

    def _handle(self, message: dict[str, Any]) -> None:
        kind = str(message.get("type", "")).lower()
        if kind == "begin":
            self.ready.set()
        elif kind == "turn":
            text, finished = self.assembler.add(message)
            if text:
                self.callbacks.partial(text)
            if finished and self._awaiting_final:
                self._deliver(self.assembler.text)
        elif kind == "termination":
            self.ready.set()
            if self._awaiting_final:
                self._deliver(self.assembler.text)
            self._closed = True
        elif kind == "error":
            self._fail(RuntimeError(str(message.get("error") or message.get("message") or "AssemblyAI error")))

    def _deliver(self, text: str) -> None:
        with self._lock:
            if self._delivered:
                return
            self._delivered = True
            if self._timer:
                self._timer.cancel()
        self.callbacks.final(text)
        self._send_json({"type": "Terminate"})
        self._close()

    def _fail(self, exc: Exception) -> None:
        if self._awaiting_final and not self._delivered and self.assembler.text:
            self._deliver(self.assembler.text)       # salvage what we heard
            return
        if not self._delivered:
            with self._lock:
                self._delivered = True
            self.callbacks.error(f"speech recognition failed: {exc}")
        self._close()

    def _send_json(self, payload: dict[str, Any]) -> None:
        if self.ws is not None and not self._closed:
            try:
                self.ws.send(json.dumps(payload))
            except Exception:
                pass

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        ws = self.ws

        def close() -> None:
            try:
                ws.close()
            except Exception:
                pass

        # The websockets close handshake can wait up to close_timeout; callers
        # include the main thread (hotkey handlers), which must never stall.
        threading.Thread(target=close, daemon=True, name="buddy-assemblyai-close").start()


def _websocket_connect(url: str):
    from websockets.sync.client import connect

    return connect(url, open_timeout=5, close_timeout=1, max_size=2 ** 20)


class MicStream:
    """16 kHz mono int16 microphone audio via PortAudio (``sounddevice``)."""

    def __init__(self, on_audio: Callable[[bytes], None], *, block: int = 800):
        import sounddevice

        self._stream = sounddevice.RawInputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=block,
            callback=lambda data, frames, when, status: on_audio(bytes(data)))

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class AssemblyAIListener:
    """Clicky's default engine. Audio recorded before the socket is ready is buffered."""

    name = "assemblyai"

    def __init__(self, api_key: str, callbacks: ListenerCallbacks, *,
                 token_source: Callable[[], str] | None = None,
                 session_factory: Callable[[str], AssemblyAISession] | None = None,
                 mic_factory: Callable[[Callable[[bytes], None]], Any] | None = None):
        self.callbacks = callbacks
        self._token_source = token_source or (lambda: fetch_assemblyai_token(api_key))
        self._session_factory = session_factory or (lambda url: AssemblyAISession(url, callbacks))
        self._mic_factory = mic_factory or MicStream
        self._next_token: tuple[float, str] | None = None
        self._session: AssemblyAISession | None = None
        self._mic = None
        self._buffer: list[bytes] = []
        self._lock = threading.Lock()
        self._generation = 0
        self.prewarm()

    def prewarm(self) -> None:
        """Fetch the next single-use token now so pressing the key connects faster."""
        def fetch():
            try:
                self._next_token = (time.monotonic(), self._token_source())
            except Exception:
                self._next_token = None
        threading.Thread(target=fetch, daemon=True).start()

    def _token(self) -> str:
        cached, self._next_token = self._next_token, None
        if cached and time.monotonic() - cached[0] < 400:
            return cached[1]
        return self._token_source()

    def start(self) -> None:
        self.cancel()
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._buffer = []
        try:
            self._mic = self._mic_factory(lambda chunk: self._on_audio(chunk, generation))
            self._mic.start()
        except Exception as exc:
            self.callbacks.error(f"microphone unavailable: {exc}")
            return
        threading.Thread(target=self._connect, args=(generation,), daemon=True).start()

    def _connect(self, generation: int) -> None:
        try:
            session = self._session_factory(assemblyai_url(self._token(), KEYTERMS))
            session.open()
        except Exception as exc:
            if generation == self._generation:
                self.callbacks.error(f"speech service unavailable: {exc}")
                self._stop_mic()
            return
        finally:
            self.prewarm()
        # Drain the backlog before publishing the session: live chunks keep
        # landing in the buffer until it is empty, so audio is never reordered.
        while True:
            with self._lock:
                if generation != self._generation:
                    session.cancel()
                    return
                backlog, self._buffer = self._buffer, []
                if not backlog:
                    self._session = session
                    break
            for chunk in backlog:
                session.send_audio(chunk)
        if self._released_generation == generation:
            session.request_final()

    _released_generation = -1

    def _on_audio(self, chunk: bytes, generation: int) -> None:
        if generation != self._generation:
            return
        self.callbacks.level(pcm16_level(chunk))
        with self._lock:
            session = self._session
            if session is None:
                self._buffer.append(chunk)
                return
        session.send_audio(chunk)

    def release(self) -> None:
        self._stop_mic()
        with self._lock:
            self._released_generation = self._generation
            session = self._session
        if session is not None:
            session.request_final()

    def cancel(self) -> None:
        self._stop_mic()
        with self._lock:
            self._generation += 1
            session, self._session = self._session, None
            self._buffer = []
        if session is not None:
            session.cancel()

    def _stop_mic(self) -> None:
        mic, self._mic = self._mic, None
        if mic is not None:
            mic.stop()


class AppleListener:
    """On-device Apple Speech via the existing AVAudioEngine/SFSpeech session."""

    name = "apple"

    def __init__(self, callbacks: ListenerCallbacks):
        from mcp_vision.speech import AppleSpeechSession

        self.callbacks = callbacks
        self._cancelled = False
        self.session = AppleSpeechSession(
            partial=lambda text, generation: self._if_current(generation, callbacks.partial, text),
            final=lambda text, reliable, generation: self._if_current(generation, callbacks.final, text),
            level=lambda value, generation: self._if_current(generation, callbacks.level, value),
            status=lambda message, generation: self._status(generation, message),
        )

    def _current(self, generation: int) -> bool:
        # The session's own counter is authoritative: it advances inside start()
        # before any status is reported, so early permission errors still count.
        return not self._cancelled and generation == self.session.generation

    def _if_current(self, generation: int, callback, value) -> None:
        if self._current(generation):
            callback(value)

    PROBLEM_WORDS = ("denied", "unavailable", "not installed", "required", "could not start", "not authorized")

    def _status(self, generation: int, message: str) -> None:
        lowered = str(message).lower()
        if self._current(generation) and any(word in lowered for word in self.PROBLEM_WORDS):
            self.callbacks.error(str(message))

    def start(self) -> None:
        self._cancelled = False
        self.session.start()

    def release(self) -> None:
        self.session.release()

    def cancel(self) -> None:
        self._cancelled = True
        self.session.cancel()


def make_listener(settings: Any, callbacks: ListenerCallbacks) -> Listener:
    choice = (getattr(settings, "stt", "auto") or "auto").lower()
    if choice == "parakeet":
        from mcp_vision.buddy import parakeet

        if not parakeet.unavailable():
            return parakeet.ParakeetListener(callbacks)       # the mic only, no Speech framework
        choice = "apple"                              # not downloaded yet: Apple's keeps you heard meanwhile
    key = getattr(settings, "assemblyai_api_key", None)
    if choice in {"auto", "assemblyai"} and key:
        try:
            import sounddevice  # noqa: F401
            import websockets  # noqa: F401
            return AssemblyAIListener(key, callbacks)
        except ImportError as exc:
            if choice == "assemblyai":
                raise RuntimeError("AssemblyAI needs the websockets package: reinstall Plip") from exc
    return AppleListener(callbacks)
