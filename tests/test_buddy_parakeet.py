"""Parakeet: the opt-in download (pinned, checked, resumable) and push-to-talk on the Mac's own model."""
from __future__ import annotations

import hashlib
import io
import random
import struct
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from mcp_vision.buddy import parakeet
from mcp_vision.buddy.parakeet import (
    PIECE, Cancelled, DownloadError, ModelFile, ModelSpec, ParakeetListener, ParakeetModel, download, installed,
    pieces, transcribe,
)
from mcp_vision.buddy.speech_in import SAMPLE_RATE, ListenerCallbacks

FILES = {"tokens.txt": b"<unk> 0\n" * 40, "encoder.int8.onnx": bytes(range(256)) * 40}


def spec(files=FILES, **sha) -> ModelSpec:
    return ModelSpec(folder="tiny", repo="someone/tiny", revision="abc123",
                     files=tuple(ModelFile(name, len(data), sha.get(name.split(".")[0], hashlib.sha256(data).hexdigest()))
                                 for name, data in files.items()))


class Hub:
    """Serves the files like Hugging Face does, Range requests included, and counts what it sent."""

    def __init__(self, files=FILES, fail_after: int | None = None):
        self.files, self.fail_after = files, fail_after
        self.requests = []

    def __call__(self, request, timeout=None):
        name = request.full_url.rsplit("/", 1)[-1]
        assert request.full_url == f"https://huggingface.co/someone/tiny/resolve/abc123/{name}"
        start = int(request.headers.get("Range", "bytes=0-")[6:-1] or 0)
        self.requests.append((name, start))
        body = self.files[name][start:]
        response = io.BytesIO(body)
        response.status = 206 if start else 200
        if self.fail_after is not None:
            limit, read = self.fail_after, response.read

            def flaky(size=-1, sent=[0]):
                if sent[0] >= limit:
                    raise ConnectionResetError("reset by peer")
                block = read(min(size, limit - sent[0]))
                sent[0] += len(block)
                return block
            response.read = flaky
        return response


# -- the download -------------------------------------------------------------------------------------

def test_it_downloads_checks_and_lands_every_file(tmp_path):
    seen = []
    hub = Hub()
    path = download(spec(), tmp_path / "model", progress=lambda done, total: seen.append((done, total)), opener=hub)
    assert installed(spec(), path)
    assert {item.name: (path / item.name).read_bytes() for item in spec().files} == FILES
    assert not list(path.glob("*.part"))
    assert seen[-1] == (spec().size, spec().size) and [done for done, _ in seen] == sorted(done for done, _ in seen)
    hub.requests.clear()
    download(spec(), path, opener=hub)
    assert hub.requests == []                                      # already there: nothing fetched again


def test_a_dropped_download_picks_up_where_it_stopped(tmp_path):
    folder = tmp_path / "model"
    with pytest.raises(DownloadError, match="pick it up where it left off"):
        download(spec(), folder, opener=Hub(fail_after=3000))
    assert not installed(spec(), folder)
    hub = Hub()
    download(spec(), folder, opener=hub)
    assert installed(spec(), folder)
    assert hub.requests == [("encoder.int8.onnx", 3000)]          # a Range request for the rest, not a restart


def test_a_file_that_doesnt_match_its_checksum_is_thrown_away(tmp_path):
    folder = tmp_path / "model"
    with pytest.raises(DownloadError, match="checksum"):
        download(spec(encoder="0" * 64), folder, opener=Hub())
    assert not (folder / "encoder.int8.onnx").exists() and not (folder / "encoder.int8.onnx.part").exists()
    assert (folder / "tokens.txt").exists()                        # the good one stays


def test_a_download_can_be_cancelled(tmp_path):
    with pytest.raises(Cancelled):
        download(spec(), tmp_path / "model", cancelled=lambda: True, opener=Hub())


def test_the_real_model_is_pinned_and_checked():
    assert parakeet.MODEL.revision and len(parakeet.MODEL.revision) == 40
    assert all(len(item.sha256) == 64 for item in parakeet.MODEL.files)
    assert {item.name for item in parakeet.MODEL.files} == {"tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx",
                                                          "joiner.int8.onnx"}
    assert 600_000_000 < parakeet.MODEL.size < 700_000_000


# -- its place in Settings ------------------------------------------------------------------------------

def test_settings_sees_missing_downloading_ready_and_removed(tmp_path):
    changes, ready, gate = [], [], threading.Event()

    def slow_fetch(model_spec, directory, *, progress, cancelled):
        progress(10, model_spec.size)
        gate.wait(5)
        return download(model_spec, directory, opener=Hub())
    model = ParakeetModel(spec=spec(), directory=tmp_path / "model", fetch=slow_fetch,
                          on_change=lambda: changes.append(1), on_ready=lambda: ready.append(1))
    assert model.snapshot()["state"] == "missing"
    model.start()
    assert model.snapshot() == {**model.snapshot(), "state": "downloading", "done": 10, "total": spec().size}
    gate.set()
    model._worker.join(5)
    assert model.snapshot()["state"] == "ready" and ready == [1] and changes
    model.remove()
    assert model.snapshot()["state"] == "missing" and not (tmp_path / "model").exists()


def test_a_failed_download_says_why(tmp_path):
    def broken(model_spec, directory, *, progress, cancelled):
        raise DownloadError("Couldn't reach Hugging Face to download Parakeet (no route).")
    model = ParakeetModel(spec=spec(), directory=tmp_path / "model", fetch=broken)
    model.start(wait=True)
    assert model.snapshot()["state"] == "failed" and "Hugging Face" in model.snapshot()["error"]


# -- listening ------------------------------------------------------------------------------------------

def pcm(seconds: float, level: int = 4000) -> bytes:
    return struct.pack(f"<{int(SAMPLE_RATE * seconds)}h", *([level, -level] * int(SAMPLE_RATE * seconds / 2)))


def noise(seconds: float, seed: int = 1) -> bytes:
    return random.Random(seed).randbytes(int(SAMPLE_RATE * seconds) * 2)


def silence(seconds: float) -> bytes:
    return bytes(int(SAMPLE_RATE * seconds) * 2)


class FakeModel:
    """Says how much audio it was given, like a recognizer that only counts. Streams read side by side."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.reads = []
        self.reading = 0                                            # reads in flight right now
        self._lock = threading.Lock()

    def create_stream(self):
        stream = types.SimpleNamespace(samples=None, result=None)
        stream.accept_waveform = lambda rate, samples: self._accept(stream, rate, samples)
        return stream

    def _accept(self, stream, rate, samples):
        assert rate == SAMPLE_RATE and all(-1.0 <= value <= 1.0 for value in samples[:50])
        stream.samples = samples

    def decode_stream(self, stream):
        with self._lock:
            self.reading += 1
        time.sleep(self.delay)
        with self._lock:
            self.reading -= 1
            self.reads.append(len(stream.samples))
        stream.result = types.SimpleNamespace(text=f" heard {len(stream.samples) / SAMPLE_RATE:.1f}  seconds,  at least$5 ")


def test_transcribe_tidies_the_text_and_skips_a_tap():
    model = FakeModel()
    assert transcribe(model, pcm(0.1)) == ""                       # a tap on the keys, not speech
    assert transcribe(model, pcm(1.0)) == "heard 1.0 seconds, at least $5"


def test_long_audio_is_read_in_pieces_and_joined():
    model = FakeModel()
    text = transcribe(model, pcm(25.0))
    assert len(model.reads) == len(pieces(pcm(25.0))) == 4 and max(model.reads) <= PIECE * SAMPLE_RATE
    assert sum(model.reads) == 25 * SAMPLE_RATE and text.count("heard") == 4 and "$5 heard" in text


# -- pieces -----------------------------------------------------------------------------------------------

def test_pieces_are_at_most_10s_and_add_up_to_the_audio_exactly():
    audio = noise(37.3) + b"\x01"                                    # a stray odd byte too
    spans = pieces(audio)
    assert len(spans) >= 4 and all(end - start <= PIECE * SAMPLE_RATE * 2 for start, end in spans)
    assert b"".join(audio[start:end] for start, end in spans) == audio
    assert all(start % 2 == 0 for start, _ in spans)                 # never splits a sample
    assert pieces(pcm(9.9)) == [(0, len(pcm(9.9)))] and pieces(b"") == [(0, 0)]


def test_a_cut_lands_in_the_pause():
    audio = noise(7.3) + silence(0.25) + noise(6.0, seed=2)
    (_, cut), (start, end) = pieces(audio)
    assert cut == start and end == len(audio)
    assert 7.3 <= cut / 2 / SAMPLE_RATE <= 7.55


def test_pieces_of_growing_audio_stay_put():
    audio = noise(33.0)
    whole = pieces(audio)
    for seconds in (10.5, 14.0, 21.7, 26.0, 32.9):
        grown = pieces(audio[: int(SAMPLE_RATE * seconds) * 2])
        assert grown[:-1] == whole[:len(grown) - 1]                # only the last piece is still changing


class FakeMic:
    def __init__(self, on_audio):
        self.on_audio, self.started, self.stopped = on_audio, False, False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def listener(model, load=None, **kw):
    heard = {"partial": [], "final": [], "level": [], "error": []}
    callbacks = ListenerCallbacks(partial=heard["partial"].append, final=heard["final"].append,
                                  level=heard["level"].append, error=heard["error"].append)
    mics = []

    def mic(on_audio):
        mics.append(FakeMic(on_audio))
        return mics[-1]
    return ParakeetListener(callbacks, model=load or (lambda: model), mic_factory=mic, **kw), heard, mics


def wait_until(check, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not check():
        time.sleep(0.01)
    return check()


def test_push_to_talk_shows_it_live_then_the_whole_thing_on_release():
    talk, heard, mics = listener(FakeModel(), partial_every=0.05)
    talk.start()
    for _ in range(10):
        mics[0].on_audio(pcm(0.1))
    assert wait_until(lambda: heard["partial"])                     # the live transcript while holding
    assert heard["level"] and max(heard["level"]) > 0
    talk.release()
    assert wait_until(lambda: heard["final"]) and mics[0].stopped
    assert heard["final"] == ["heard 1.0 seconds, at least $5"] and heard["error"] == []


def test_letting_go_of_another_shortcut_hears_nothing():
    model = FakeModel(delay=0.05)
    talk, heard, mics = listener(model, partial_every=10)
    talk.start()
    mics[0].on_audio(pcm(1.0))
    talk.release()
    talk.cancel()                                                   # it was ⌃⌥ + another key after all
    time.sleep(0.2)
    assert heard["final"] == []
    talk.start()                                                    # the next press starts clean
    mics[1].on_audio(pcm(0.5))
    mics[0].on_audio(pcm(3.0))                                      # the old mic's late audio is ignored
    talk.release()
    assert wait_until(lambda: heard["final"]) and heard["final"] == ["heard 0.5 seconds, at least $5"]


def test_a_model_that_wont_load_is_an_error_not_a_crash():
    def broken():
        raise RuntimeError("encoder.int8.onnx is corrupt")
    talk, heard, mics = listener(None, load=broken, partial_every=10)
    talk.start()
    mics[0].on_audio(pcm(1.0))
    talk.release()
    assert wait_until(lambda: heard["error"]) and "corrupt" in heard["error"][0] and heard["final"] == []


def test_a_tap_doesnt_wait_for_the_model():
    loaded = threading.Event()
    talk, heard, mics = listener(None, load=lambda: loaded.wait(5), partial_every=10)
    talk.start()
    mics[0].on_audio(pcm(0.1))
    talk.release()
    assert wait_until(lambda: heard["final"], timeout=1) and heard["final"] == [""]
    loaded.set()


# -- loading it when you press, letting it go when you don't ----------------------------------------------

def slow_load(model, seconds):
    """Like recognizer(): the first call loads (slowly), the rest wait for it, then it's there."""
    loads, lock = [], threading.Lock()

    def load():
        with lock:
            if not loads:
                time.sleep(seconds)
                loads.append(time.monotonic())
        return model
    return load, loads


def test_the_model_loads_on_the_first_press_and_letting_go_early_waits_for_it():
    model = FakeModel(delay=0.1)
    load, loads = slow_load(model, 0.3)
    talk, heard, mics = listener(model, load=load, partial_every=0.01)
    time.sleep(0.1)
    assert loads == []                                              # nothing loaded until they press
    pressed = time.monotonic()
    talk.start()
    mics[0].on_audio(pcm(1.0))
    time.sleep(0.1)                                                 # the live loop is waiting for it too
    talk.release()                                                  # before it finished loading
    assert wait_until(lambda: heard["final"])
    assert loads[0] - pressed >= 0.3 and heard["final"] == ["heard 1.0 seconds, at least $5"]
    assert heard["partial"] == [] and model.reads == [SAMPLE_RATE]  # no live read once they let go


def test_partials_start_once_its_loaded():
    model = FakeModel()
    load, loads = slow_load(model, 0.3)
    talk, heard, mics = listener(model, load=load, partial_every=0.01)
    talk.start()
    mics[0].on_audio(pcm(1.0))
    assert wait_until(lambda: heard["partial"])
    assert loads and heard["partial"][0] == "heard 1.0 seconds, at least $5"
    talk.cancel()


def test_an_idle_model_is_let_go_but_never_mid_read():
    model = FakeModel(delay=0.3)
    unloads = []
    talk, heard, mics = listener(model, partial_every=0.01, idle_unload=0.02,
                                 unload=lambda: unloads.append(model.reading))
    talk.start()
    mics[0].on_audio(pcm(2.0))
    assert wait_until(lambda: model.reading)                        # a live read is running
    talk.cancel()                                                   # idle from here: the timer fires mid-read
    assert wait_until(lambda: unloads)
    assert unloads == [0] and model.reads                           # it waited for that read to finish


def test_pressing_again_keeps_it_loaded():
    unloads = []
    talk, heard, mics = listener(FakeModel(), partial_every=10, idle_unload=0.2, unload=lambda: unloads.append(1))
    talk.start()
    mics[0].on_audio(pcm(1.0))
    talk.release()
    assert wait_until(lambda: heard["final"])
    talk.start()                                                    # back before it went idle
    time.sleep(0.4)
    assert unloads == []
    talk.release()
    assert wait_until(lambda: unloads)
    time.sleep(0.3)
    assert unloads == [1]


def test_an_old_listener_doesnt_pull_the_model_from_a_new_one():
    unloads = []
    old, heard, mics = listener(FakeModel(), partial_every=10, idle_unload=0.2, unload=lambda: unloads.append("old"))
    old.start()
    mics[0].on_audio(pcm(1.0))
    old.release()
    assert wait_until(lambda: heard["final"])
    new, _, new_mics = listener(FakeModel(), partial_every=10, idle_unload=0.2, unload=lambda: unloads.append("new"))
    new.start()                                                     # settings changed: a new listener, in use
    time.sleep(0.4)
    assert unloads == []
    new.cancel()
    assert wait_until(lambda: unloads) and unloads == ["new"]


# -- reading only what's new, and letting go without waiting ------------------------------------------------

def test_the_live_transcript_only_rereads_the_newest_piece_and_so_does_the_final():
    model = FakeModel()
    talk, heard, mics = listener(model, partial_every=0.02)
    talk.start()
    mics[0].on_audio(pcm(25.0))
    assert wait_until(lambda: heard["partial"])
    assert len(model.reads) == len(pieces(pcm(25.0))) == 4         # every piece, once
    mics[0].on_audio(pcm(0.5))
    assert wait_until(lambda: len(heard["partial"]) == 2)
    start, end = pieces(pcm(25.5))[-1]
    assert len(model.reads) == 5 and model.reads[-1] == (end - start) // 2     # just the newest piece again
    mics[0].on_audio(pcm(0.5))
    talk.release()
    assert wait_until(lambda: heard["final"])
    start, end = pieces(pcm(26.0))[-1]
    assert set(model.reads[5:]) == {(end - start) // 2}            # the final too (or a live read just before it)
    assert heard["final"][0].count("heard") == 4


def test_letting_go_doesnt_wait_for_a_live_read():
    model = FakeModel(delay=0.4)
    talk, heard, mics = listener(model, partial_every=0.01)
    talk.start()
    mics[0].on_audio(pcm(2.0))
    assert wait_until(lambda: model.reading)                        # a live read just started
    let_go = time.monotonic()
    talk.release()
    assert wait_until(lambda: heard["final"])
    assert time.monotonic() - let_go < 0.65                         # its own read only, not 0.4 + 0.4
    time.sleep(0.5)
    assert len(model.reads) == 2 and heard["partial"] == []        # the live one's text dropped, no new one


# -- picking it ---------------------------------------------------------------------------------------------

class Settings:
    def __init__(self, stt="parakeet", key=None):
        self.stt, self.assemblyai_api_key = stt, key


def test_picking_parakeet_listens_with_it_and_never_touches_apple_speech(monkeypatch):
    from mcp_vision.buddy import speech_in

    monkeypatch.setattr(parakeet, "unavailable", lambda *args, **kw: "")
    monkeypatch.setattr(parakeet, "ParakeetListener", lambda callbacks: types.SimpleNamespace(name="parakeet"))
    monkeypatch.setattr(speech_in, "AppleListener", lambda callbacks: pytest.fail("used Apple Speech"))
    assert speech_in.make_listener(Settings(), ListenerCallbacks()).name == "parakeet"


def test_until_its_downloaded_apples_listens(monkeypatch):
    from mcp_vision.buddy import speech_in

    monkeypatch.setattr(parakeet, "unavailable", lambda *args, **kw: "Parakeet isn't downloaded yet")
    monkeypatch.setattr(speech_in, "AppleListener", lambda callbacks: types.SimpleNamespace(name="apple"))
    assert speech_in.make_listener(Settings(), ListenerCallbacks()).name == "apple"
    assert speech_in.make_listener(Settings(key="aai"), ListenerCallbacks()).name == "apple"   # not AssemblyAI either


def test_settings_downloads_switches_and_removes(tmp_path):
    from mcp_vision.buddy.settings import BuddySettings
    from mcp_vision.buddy.settings_service import SettingsService
    from mcp_vision.buddy.store import Prefs

    class Model:
        def __init__(self):
            self.calls = []

        def snapshot(self):
            return {"state": "missing", "done": 0, "total": 663_043_117, "error": "", "runtime": True}

        def start(self):
            self.calls.append("start")

        def cancel(self):
            self.calls.append("cancel")

        def remove(self):
            self.calls.append("remove")
    model, posted, reloads = Model(), [], []
    service = SettingsService(engines=lambda: [], settings=lambda: BuddySettings(_env_file=None),
                              reload=lambda: reloads.append(1), post=posted.extend, prefs_path=tmp_path / "prefs.json",
                              parakeet=model)
    assert service.snapshot()["voice"]["parakeet"]["state"] == "missing"
    service.handle({"cmd": "parakeet-download"})
    assert model.calls == ["start"] and Prefs.load(tmp_path / "prefs.json").stt == "parakeet"
    service.handle({"cmd": "parakeet-cancel"})
    service.handle({"cmd": "parakeet-remove"})
    assert model.calls == ["start", "cancel", "remove"] and Prefs.load(tmp_path / "prefs.json").stt == "apple"
    service.handle({"cmd": "set-voice", "stt": "parakeet"})
    assert Prefs.load(tmp_path / "prefs.json").stt == "parakeet" and reloads


# The tests run in a sandboxed state folder; the real download lives in the real one.
REAL_MODEL = Path.home() / ".local/share/mcp-vision/models" / parakeet.MODEL.folder


@pytest.mark.skipif(sys.platform != "darwin" or not installed(directory=REAL_MODEL) or not parakeet.runtime_available(),
                    reason="needs the downloaded model and sherpa-onnx")
def test_the_real_model_hears_a_spoken_request(tmp_path):
    import subprocess
    import wave

    aiff, wav = tmp_path / "ask.aiff", tmp_path / "ask.wav"
    subprocess.run(["say", "-o", str(aiff), "Open Spotify and play my Discover Weekly."], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
    with wave.open(str(wav)) as audio:
        text = transcribe(parakeet.recognizer(REAL_MODEL), audio.readframes(audio.getnframes()))
    assert text == "Open Spotify and play my Discover Weekly."
