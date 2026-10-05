"""Parakeet: NVIDIA's Parakeet Unified 0.6B as Plip's ear, on your Mac.

An opt-in alternative to Apple's on-device recognition. ``nvidia/parakeet-unified-en-0.6b`` is an
English FastConformer-RNNT that writes punctuation and capitals, and nothing you say leaves the
Mac. It's big, so nothing is downloaded until you pick it: the int8 ONNX export by sherpa-onnx's
author (about 663 MB), pinned to one revision and checked against its SHA-256 file by file,
resumable, into Plip's own folder.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
