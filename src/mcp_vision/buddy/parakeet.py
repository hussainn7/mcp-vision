"""Parakeet: NVIDIA's Parakeet Unified 0.6B as Plip's ear, on your Mac.

An opt-in alternative to Apple's on-device recognition. ``nvidia/parakeet-unified-en-0.6b`` is an
English FastConformer-RNNT that writes punctuation and capitals, and nothing you say leaves the
Mac. It's big, so nothing is downloaded until you pick it: the int8 ONNX export by sherpa-onnx's
author (about 663 MB), pinned to one revision and checked against its SHA-256 file by file,
resumable, into Plip's own folder.

sherpa-onnx runs it on the CPU. Push-to-talk works like the other listeners: the microphone is
buffered while you hold Control + Option, the live transcript comes from re-reading what's there
so far (at most every 0.6 s), and the final one from the whole utterance when you let go. Both read
it in pieces of 10 s at most, cut where it's quietest: a piece read once isn't read again, and the
model's scratch memory stays small however long you talk. The model (about 1.2 GB loaded) loads
when you press, while you talk, and is let go after 5 minutes unused. It only needs the microphone,
not Speech Recognition, so it works from a Terminal too.
"""
from __future__ import annotations

import array
import gc
import hashlib
import importlib.util
import os
import re
import shutil
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp_vision.buddy.speech_in import SAMPLE_RATE, ListenerCallbacks, MicStream, pcm16_level


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelSpec:
    folder: str
    repo: str
    revision: str
    files: tuple[ModelFile, ...]

    @property
    def size(self) -> int:
        return sum(item.size for item in self.files)

    def url(self, item: ModelFile) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{item.name}"


MODEL = ModelSpec(
    folder="parakeet-unified-en-0.6b-int8",
    repo="csukuangfj2/sherpa-onnx-nemo-parakeet-unified-en-0.6b-int8-non-streaming",
    revision="8c3a10fb13408c7a7054f6898958bf1c64a8d6c7",
    files=(
        ModelFile("tokens.txt", 8_952, "dc0b4584ab2e4ddbf888425c076c61b736e7356a015250db7d307e6f1a8188ff"),
        ModelFile("decoder.int8.onnx", 7_257_753, "a5e223392c90e75f8144cdb5eb95af7625db389e39edef2bd1a9c872b3298fe6"),
        ModelFile("joiner.int8.onnx", 1_735_860, "869f43f7d24595c55581ad3bf249a935fb8a71389fbdaa7504b9f46f93140f8a"),
        ModelFile("encoder.int8.onnx", 654_040_552,
                  "6716910b7a0833997fec7a410494c995d70124001a0e9b66d6370d6aced577e0"),
    ),
)
CHUNK = 1 << 20                      # bytes per read while downloading
MIN_AUDIO = 0.25                     # seconds: shorter than this is a tap, not speech
MIN_PIECE = 0.02                     # seconds: a sliver reads as nothing (the model throws under ~5 ms)
PARTIAL_EVERY = 0.6                  # seconds between live transcripts, at the least
PIECE = 10.0                         # seconds read at once, at most: the model's scratch memory grows with it
PIECE_SEARCH = 4.0                   # a piece ends at the quietest frame in its last 4 s
PIECE_FRAME = 0.03                   # seconds per frame there
IDLE_UNLOAD = 300.0                  # seconds unused before the loaded model (about 1.2 GB) is let go
TAIL = 0.2                           # seconds of mic after release, for the last word
_SCALE = 1 / 32768                   # int16 -> -1..1
_GLUED_MONEY = re.compile(r"(?<=[A-Za-z])(?=[$€£]\d)")


def models_dir() -> Path:
    from mcp_vision.paths import state_dir

    return state_dir() / "models"


def model_dir(spec: ModelSpec = MODEL) -> Path:
    return models_dir() / spec.folder


def installed(spec: ModelSpec = MODEL, directory: Path | None = None) -> bool:
    """Every file there at its full size (the hashes were checked when it was downloaded)."""
    directory = directory or model_dir(spec)
    return all((directory / item.name).is_file() and (directory / item.name).stat().st_size == item.size
               for item in spec.files)


def runtime_available() -> bool:
    return importlib.util.find_spec("sherpa_onnx") is not None


def unavailable(spec: ModelSpec = MODEL, directory: Path | None = None) -> str:
    """Why Parakeet can't listen right now, or "" when it can."""
    if not runtime_available():
        return "Parakeet needs sherpa-onnx: reinstall Plip"
    if not installed(spec, directory):
        return "Parakeet isn't downloaded yet"
    return ""


class DownloadError(RuntimeError):
    """Said in Settings as-is."""


class Cancelled(Exception):
    pass


def download(spec: ModelSpec = MODEL, directory: Path | None = None, *,
             progress: Callable[[int, int], None] = lambda done, total: None,
             cancelled: Callable[[], bool] = lambda: False,
             opener: Callable[..., Any] = urllib.request.urlopen) -> Path:
    """Fetch every file the model needs, resuming partial ones, and check each one's SHA-256.

    Files land as ``<name>.part`` and are renamed only once their hash matches, so a folder never
    holds a half-written or tampered model under its real name.
    """
    directory = directory or model_dir(spec)
    directory.mkdir(parents=True, exist_ok=True)
    total = spec.size
    done_before = 0
    for item in spec.files:
        final = directory / item.name
        if final.is_file() and final.stat().st_size == item.size:
            done_before += item.size
            progress(done_before, total)
            continue
        part = directory / f"{item.name}.part"
        _fetch(spec.url(item), part, item.size,
               lambda got, base=done_before: progress(base + got, total), cancelled, opener)
        if _sha256(part) != item.sha256:
            part.unlink(missing_ok=True)
            raise DownloadError(f"{item.name} didn't match its checksum, so I threw it away. Try again.")
        os.replace(part, final)
        done_before += item.size
        progress(done_before, total)
    return directory


def _fetch(url: str, part: Path, size: int, progress: Callable[[int], None], cancelled: Callable[[], bool],
           opener: Callable[..., Any]) -> None:
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    if have == size:
        return
    request = urllib.request.Request(url, headers={"User-Agent": "plip", **({"Range": f"bytes={have}-"} if have else {})})
    try:
        response = opener(request, timeout=30)
    except Exception as exc:
        raise DownloadError(f"Couldn't reach Hugging Face to download Parakeet ({_reason(exc)}).") from exc
    with response:
        if have and getattr(response, "status", 200) != 206:
            have = 0                                  # the server ignored the range: start that file over
        with open(part, "ab" if have else "wb") as handle:
            got = have
            progress(got)
            while True:
                if cancelled():
                    raise Cancelled()
                try:
                    block = response.read(CHUNK)
                except Exception as exc:
                    raise DownloadError(f"The download stopped ({_reason(exc)}). Try again to pick it up "
                                        "where it left off.") from exc
                if not block:
                    break
                handle.write(block)
                got += len(block)
                progress(got)
    if got != size:
        raise DownloadError("The download stopped early. Try again to pick it up where it left off.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _reason(exc: Exception) -> str:
    text = str(getattr(exc, "reason", "") or exc).strip()
    return text[:80] or type(exc).__name__


class ParakeetModel:
    """The model's place in Settings: missing, downloading (with progress), ready, or failed.

    ``on_change`` fires a few times a second while downloading; ``on_ready`` once it's in place.
    """

    PUSH_EVERY = 0.25

    def __init__(self, *, on_change: Callable[[], None] = lambda: None, on_ready: Callable[[], None] = lambda: None,
                 spec: ModelSpec = MODEL, directory: Path | None = None,
                 fetch: Callable[..., Path] = download, clock: Callable[[], float] = time.monotonic):
        self.spec = spec
        self.directory = directory or model_dir(spec)
        self.on_change = on_change
        self.on_ready = on_ready
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._done = 0
        self._error = ""
        self._pushed = 0.0

    @property
    def downloading(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def snapshot(self) -> dict[str, Any]:
        if self.downloading:
            state = "downloading"
        elif installed(self.spec, self.directory):
            state = "ready"
        elif self._error:
            state = "failed"
        else:
            state = "missing"
        return {"state": state, "done": self._done if state == "downloading" else 0, "total": self.spec.size,
                "error": self._error if state == "failed" else "", "runtime": runtime_available()}

    def start(self, *, wait: bool = False) -> None:
        with self._lock:
            if self.downloading or installed(self.spec, self.directory):
                return
            self._cancel.clear()
            self._error, self._done = "", 0
            worker = self._worker = threading.Thread(target=self._run, daemon=True, name="plip-parakeet-download")
            worker.start()
        self.on_change()
        if wait:
            worker.join()

    def cancel(self) -> None:
        self._cancel.set()

    def remove(self) -> None:
        """Delete it (the half-downloaded parts too). Frees about 663 MB."""
        self.cancel()
        worker = self._worker
        if worker is not None:
            worker.join(10)
        shutil.rmtree(self.directory, ignore_errors=True)
        self._error, self._done = "", 0
        forget_recognizer()
        self.on_change()

    def _progress(self, done: int, _total: int) -> None:
        self._done = done
        now = self._clock()
        if now - self._pushed >= self.PUSH_EVERY:
            self._pushed = now
            self.on_change()

    def _run(self) -> None:
        try:
            self._fetch(self.spec, self.directory, progress=self._progress, cancelled=self._cancel.is_set)
        except Cancelled:
            self._error = ""
        except DownloadError as exc:
            self._error = str(exc)
        except Exception as exc:                           # disk full, permissions: say so, don't crash
            self._error = f"Couldn't save Parakeet ({_reason(exc)})."
        else:
            self._worker = None
            self.on_change()
            self.on_ready()
            return
        self._worker = None
        self.on_change()


# -- recognition ----------------------------------------------------------------------------------

_RECOGNIZER: dict[str, Any] = {}
_RECOGNIZER_LOCK = threading.Lock()
_IN_USE = threading.Condition()                       # held to start a read of the model, or to let it go
_USE = {"reads": 0, "presses": 0}                     # reads running now (loads too), presses so far (any listener)


def recognizer(directory: Path | None = None, threads: int | None = None):
    """The loaded model, shared by every listener in this process (loading it takes a second or two)."""
    directory = directory or model_dir()
    with _RECOGNIZER_LOCK:
        loaded = _RECOGNIZER.get(str(directory))
        if loaded is None:
            import sherpa_onnx

            loaded = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(directory / "encoder.int8.onnx"), decoder=str(directory / "decoder.int8.onnx"),
                joiner=str(directory / "joiner.int8.onnx"), tokens=str(directory / "tokens.txt"),
                num_threads=threads or max(2, min(8, (os.cpu_count() or 4) - 2)), sample_rate=SAMPLE_RATE,
                feature_dim=128, decoding_method="greedy_search", model_type="nemo_transducer")   # the model's feat_dim
            _RECOGNIZER[str(directory)] = loaded
        return loaded


def forget_recognizer() -> None:
    with _RECOGNIZER_LOCK:
        _RECOGNIZER.clear()


@contextmanager
def _reading() -> Iterator[None]:
    """Around every use of the model, so letting it go waits for the reads in flight."""
    with _IN_USE:
        _USE["reads"] += 1
    try:
        yield
    finally:
        with _IN_USE:
            _USE["reads"] -= 1
            _IN_USE.notify_all()


def pieces(pcm16: bytes, max_s: float = PIECE, search_s: float = PIECE_SEARCH,
           frame_s: float = PIECE_FRAME) -> list[tuple[int, int]]:
    """Where to cut audio so no piece is longer than ``max_s``: (start, end) byte offsets covering all of it.

    Each cut is the middle of the quietest ``frame_s`` frame in the last ``search_s`` of a piece, so it falls
    between words. A cut only looks at the audio before it, so while the audio grows only the last piece
    changes: that's what lets the live transcript keep the pieces it already read.
    """
    samples = array.array("h")
    samples.frombytes(pcm16[: len(pcm16) // 2 * 2])
    longest, search, frame = (int(seconds * SAMPLE_RATE) for seconds in (max_s, search_s, frame_s))
    spans, start = [], 0
    while len(samples) - start > longest:
        cut = _quietest(samples, start + longest - search, start + longest, frame)
        spans.append((start * 2, cut * 2))
        start = cut
    spans.append((start * 2, len(pcm16)))
    return spans


def _quietest(samples: array.array, lo: int, hi: int, frame: int) -> int:
    """The middle of the quietest whole frame between ``lo`` and ``hi``."""
    quiet = min(range(lo, hi - frame + 1, frame), key=lambda at: sum(map(abs, samples[at:at + frame])))
    return quiet + frame // 2


def _read(model: Any, pcm16: bytes) -> str:
    """One piece -> its text."""
    samples = array.array("h")
    samples.frombytes(pcm16[: len(pcm16) // 2 * 2])
    if len(samples) < SAMPLE_RATE * MIN_PIECE:
        return ""
    stream = model.create_stream()
    stream.accept_waveform(SAMPLE_RATE, array.array("f", map(_SCALE.__mul__, samples)))   # 4 bytes a sample
    model.decode_stream(stream)
    return " ".join(str(stream.result.text).split())


def _hear(load: Callable[[], Any], pcm16: bytes, texts: dict[tuple[int, int], str],
          wanted: Callable[[], bool] = lambda: True) -> str | None:
    """The text of the whole thing, a piece at a time. Pieces already in ``texts`` (span -> text) aren't read
    again and the rest are added. None when ``wanted()`` turns false before a piece: nobody needs it now."""
    if len(pcm16) // 2 < SAMPLE_RATE * MIN_AUDIO:
        return ""                                     # a tap on the keys, not speech
    spans = pieces(pcm16)
    with _reading():
        model = load()
        for start, end in spans:
            if not wanted():
                return None
            if (start, end) not in texts:
                texts[start, end] = _read(model, pcm16[start:end])
    text = " ".join(texts[span] for span in spans if texts[span])
    return _GLUED_MONEY.sub(" ", text)                # "at least$150,000" -> "at least $150,000"


def transcribe(model: Any, pcm16: bytes) -> str:
    """16 kHz mono int16 audio -> text, with Parakeet's own punctuation and capitals."""
    return _hear(lambda: model, pcm16, {}) or ""


class ParakeetListener:
    """Push-to-talk with Parakeet. Same contract as the Apple and AssemblyAI listeners."""

    name = "parakeet"

    def __init__(self, callbacks: ListenerCallbacks, *, model: Callable[[], Any] = recognizer,
                 unload: Callable[[], None] = forget_recognizer,
                 mic_factory: Callable[[Callable[[bytes], None]], Any] = MicStream,
                 partial_every: float = PARTIAL_EVERY, idle_unload: float = IDLE_UNLOAD, tail: float = TAIL):
        self.callbacks = callbacks
        self.tail = tail
        self._model = model
        self._unload = unload
        self._mic_factory = mic_factory
        self.partial_every = partial_every
        self.idle_unload = idle_unload
        self._lock = threading.Lock()
        self._generation = 0
        self._audio = bytearray()
        self._texts: dict[tuple[int, int], str] = {}     # this press's pieces read so far
        self._mic = None
        self._idle_timer: threading.Timer | None = None

    def start(self) -> None:
        generation = self._reset()
        self._keep_loaded()
        try:
            self._mic = self._mic_factory(lambda chunk: self._on_audio(chunk, generation))
            self._mic.start()
        except Exception as exc:
            self.callbacks.error(f"microphone unavailable: {exc}")
            return
        threading.Thread(target=self._load, daemon=True, name="plip-parakeet-load").start()
        threading.Thread(target=self._live, args=(generation,), daemon=True, name="plip-parakeet-live").start()

    def _load(self) -> None:
        """Load the model while they talk (about a second), so letting go doesn't wait for it."""
        try:
            with _reading():
                self._model()
        except Exception:
            pass                                          # the final read reports it if it still can't load

    def _on_audio(self, chunk: bytes, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._audio.extend(chunk)
        self.callbacks.level(pcm16_level(chunk))

    def _snapshot(self, generation: int) -> tuple[bytes, dict[tuple[int, int], str]] | None:
        with self._lock:
            return (bytes(self._audio), self._texts) if generation == self._generation else None

    def _listening(self, generation: int) -> bool:
        return generation == self._generation and self._mic is not None

    def _live(self, generation: int) -> None:
        """While the keys are held: the transcript so far, re-read now and then (just its newest piece)."""
        heard = 0
        wait = self.partial_every
        while True:
            time.sleep(wait)
            snapshot = self._snapshot(generation)
            if snapshot is None or self._mic is None:
                return
            audio, texts = snapshot
            if len(audio) == heard:
                continue
            heard = len(audio)
            started = time.monotonic()
            try:
                text = _hear(self._model, audio, texts, lambda: self._listening(generation))
            except Exception:
                return                                    # the final read reports anything real
            if text is None:
                return                                    # let go mid-read: the final has it from here
            # A long hold takes longer to re-read: space the reads out so the Mac stays responsive.
            wait = max(self.partial_every, (time.monotonic() - started) * 2)
            if text and self._listening(generation):
                self.callbacks.partial(text)

    def release(self) -> None:
        generation = self._generation
        if self.tail <= 0:
            self._finish(generation)
            return
        timer = threading.Timer(self.tail, self._finish, args=(generation,))
        timer.daemon = True
        timer.start()

    def _finish(self, generation: int) -> None:
        if generation != self._generation:
            return                                        # cancelled or pressed again
        self._stop_mic()                                  # the live loop stops before its next read
        with self._lock:
            audio, texts = bytes(self._audio), self._texts
        threading.Thread(target=self._final, args=(generation, audio, texts), daemon=True,
                         name="plip-parakeet-final").start()

    def _final(self, generation: int, audio: bytes, texts: dict[tuple[int, int], str]) -> None:
        # Its own stream, next to a live read still finishing (sherpa-onnx reads streams side by side):
        # it never waits for that one, and only reads the pieces the live transcript hasn't.
        try:
            text = _hear(self._model, audio, texts, lambda: generation == self._generation)
        except Exception as exc:
            if generation == self._generation:
                self.callbacks.error(f"speech recognition failed: {exc}")
            return
        finally:
            self._unload_later()
        if text is not None and generation == self._generation:
            self.callbacks.final(text)

    def cancel(self) -> None:
        self._reset()
        self._unload_later()

    def _reset(self) -> int:
        """Stop listening and drop this press: its late audio, reads and live loop see a newer generation."""
        self._stop_mic()
        with self._lock:
            self._generation += 1
            self._audio, self._texts = bytearray(), {}
            return self._generation

    def _stop_mic(self) -> None:
        mic, self._mic = self._mic, None
        if mic is not None:
            mic.stop()

    # -- letting the model go when nobody's talking ----------------------------------------------------

    def _keep_loaded(self) -> None:
        """A press: this listener's idle timer stops, and any other listener's stands down when it fires (a
        settings change builds a new listener while the old one's timer still runs)."""
        with _IN_USE:
            _USE["presses"] += 1
        with self._lock:
            timer, self._idle_timer = self._idle_timer, None
        if timer is not None:
            timer.cancel()

    def _unload_later(self) -> None:
        timer = threading.Timer(self.idle_unload, self._idle, args=(_USE["presses"],))
        timer.daemon = True
        with self._lock:
            old, self._idle_timer = self._idle_timer, timer
        if old is not None:
            old.cancel()
        timer.start()

    def _idle(self, presses: int) -> None:
        """Nobody's pressed for ``idle_unload``: let the model go (about 1.2 GB) until the next press loads it."""
        with _IN_USE:
            _IN_USE.wait_for(lambda: not _USE["reads"])  # never mid-read: the ones in flight finish first
            if presses != _USE["presses"] or self._mic is not None:
                return                                    # pressed again since, here or on a newer listener
            self._unload()
        gc.collect()
