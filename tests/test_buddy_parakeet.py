"""Parakeet: the opt-in download (pinned, checked, resumable, cancellable)."""
from __future__ import annotations

import hashlib
import io
import threading

import pytest

from mcp_vision.buddy import parakeet
from mcp_vision.buddy.parakeet import Cancelled, DownloadError, ModelFile, ModelSpec, ParakeetModel, download, installed

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
