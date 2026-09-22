"""Hold-to-talk coordination and the native Apple Speech adapter."""
from __future__ import annotations

import math
import threading
from collections.abc import Callable
from typing import Any


class HoldToTalk:
    """Distinguish a shortcut tap from a hold without depending on AppKit."""

    def __init__(self, schedule: Callable[[float, Callable[[], None]], Any], *, threshold: float = 0.2,
                 on_tap: Callable[[Any], None], on_hold: Callable[[Any], None],
                 on_release: Callable[[], None]):
        self.schedule = schedule
        self.threshold = threshold
        self.on_tap = on_tap
        self.on_hold = on_hold
        self.on_release = on_release
        self.state = "idle"
        self.context = None
        self.generation = 0

    def press(self, context: Any) -> bool:
        if self.state != "idle":
            return False
        self.generation += 1
        generation = self.generation
        self.context = context
        self.state = "pressed"

        def held() -> None:
            if generation != self.generation or self.state != "pressed":
                return
            self.state = "listening"
            self.on_hold(self.context)

        self.schedule(self.threshold, held)
        return True

    def release(self) -> bool:
        if self.state == "pressed":
            context = self.context
            self._reset()
            self.on_tap(context)
            return True
        if self.state == "listening":
            self.state = "finalizing"
            self.on_release()
            return True
        return False

    def finish(self) -> None:
        self._reset()

    def cancel(self) -> None:
        self.generation += 1
        self._reset(increment=False)

    def _reset(self, *, increment: bool = True) -> None:
        if increment:
            self.generation += 1
        self.state = "idle"
        self.context = None


class AppleSpeechSession:
    """One streaming recognition session using AVAudioEngine and Speech."""

    def __init__(self, *, partial: Callable[[str, int], None], final: Callable[[str, bool, int], None],
                 level: Callable[[float, int], None], status: Callable[[str, int], None],
                 timeout: float = 2.5):
        self.partial_callback = partial
        self.final_callback = final
        self.level_callback = level
        self.status_callback = status
        self.timeout = timeout
        self.lock = threading.RLock()
        self.generation = 0
        self.best_text = ""
        self.released = False
        self.completed = False
        self.engine = None
        self.request = None
        self.task = None
        self.recognizer = None
        self.input_node = None
        self.pending_final: tuple[str, bool] | None = None

    def start(self) -> int:
        with self.lock:
            self.cancel()
            self.generation += 1
            generation = self.generation
            self.best_text = ""
            self.released = False
            self.completed = False
            self.pending_final = None
        try:
            import AVFoundation
            import Speech
        except ImportError:
            self.status_callback("Speech support is not installed. Reinstall MCP-Vision on macOS.", generation)
            self.pending_final = ("", False)
            return generation

        self.status_callback("Checking microphone access…", generation)
        permissions: dict[str, bool | None] = {"speech": None, "microphone": None}

        def ready(name: str, allowed: bool) -> None:
            with self.lock:
                if generation != self.generation:
                    return
                permissions[name] = allowed
                if any(value is None for value in permissions.values()):
                    return
                if not all(permissions.values()):
                    self.status_callback("Microphone and Speech Recognition access are required.", generation)
                    self.pending_final = ("", False)
                    if self.released:
                        self._complete("", False, generation=generation)
                    return
                if self.released:
                    self.status_callback("Permissions are ready. Hold Option-Space again to speak.", generation)
                    self._complete("", False, generation=generation)
                    return
                self._begin_audio(generation, AVFoundation, Speech)

        speech_status = int(Speech.SFSpeechRecognizer.authorizationStatus())
        if speech_status == int(Speech.SFSpeechRecognizerAuthorizationStatusNotDetermined):
            Speech.SFSpeechRecognizer.requestAuthorization_(
                lambda value: ready("speech", int(value) == int(Speech.SFSpeechRecognizerAuthorizationStatusAuthorized)))
        else:
            ready("speech", speech_status == int(Speech.SFSpeechRecognizerAuthorizationStatusAuthorized))

        media_type = AVFoundation.AVMediaTypeAudio
        microphone_status = int(AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(media_type))
        if microphone_status == int(AVFoundation.AVAuthorizationStatusNotDetermined):
            AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                media_type, lambda allowed: ready("microphone", bool(allowed)))
        else:
            ready("microphone", microphone_status == int(AVFoundation.AVAuthorizationStatusAuthorized))
        return generation

    def _begin_audio(self, generation: int, AVFoundation: Any, Speech: Any) -> None:
        try:
            recognizer = Speech.SFSpeechRecognizer.alloc().init()
            if recognizer is None or not recognizer.isAvailable():
                raise RuntimeError("Speech recognition is currently unavailable.")
            request = Speech.SFSpeechAudioBufferRecognitionRequest.alloc().init()
            request.setShouldReportPartialResults_(True)
            # Do not require a network service or an on-device asset. macOS may
            # use local recognition when available and fall back to Apple Speech.
            engine = AVFoundation.AVAudioEngine.alloc().init()
            input_node = engine.inputNode()
            audio_format = input_node.outputFormatForBus_(0)
            meter_float32 = int(audio_format.commonFormat()) == int(AVFoundation.AVAudioPCMFormatFloat32)

            def audio(buffer, _when) -> None:
                if generation != self.generation:
                    return
                request.appendAudioPCMBuffer_(buffer)
                self.level_callback(self._level(buffer) if meter_float32 else 0.0, generation)

            input_node.installTapOnBus_bufferSize_format_block_(0, 1024, audio_format, audio)
            engine.prepare()
            ok, error = engine.startAndReturnError_(None)
            if not ok:
                raise RuntimeError(str(error or "The microphone could not start."))

            self.engine = engine
            self.request = request
            self.input_node = input_node
            self.recognizer = recognizer

            def recognized(result, error) -> None:
                with self.lock:
                    if generation != self.generation or self.completed:
                        return
                    if result is not None:
                        text = str(result.bestTranscription().formattedString() or "").strip()
                        if text:
                            self.best_text = text
                            self.partial_callback(text, generation)
                        if result.isFinal():
                            self.pending_final = (text, True)
                            self._stop_audio(cancel_task=False)
                            if self.released:
                                self._complete(text, True, generation=generation)
                            return
                    if error is not None:
                        self.pending_final = (self.best_text, False)
                        self.status_callback(str(error), generation)
                        self._stop_audio(cancel_task=False)
                        if self.released:
                            self._complete(self.best_text, False, generation=generation)

            self.task = recognizer.recognitionTaskWithRequest_resultHandler_(request, recognized)
            self.status_callback("Listening…", generation)
        except Exception as exc:
            self.status_callback(str(exc), generation)
            self.pending_final = ("", False)
            self._stop_audio()
            if self.released:
                self._complete("", False, generation=generation)

    def release(self) -> None:
        with self.lock:
            self.released = True
            generation = self.generation
            if self.pending_final is not None:
                text, reliable = self.pending_final
                self._complete(text, reliable, generation=generation)
                return
            if self.request is None:
                return
            self._stop_engine()
            self.request.endAudio()

        def timed_out() -> None:
            with self.lock:
                if generation == self.generation and not self.completed:
                    self._complete(self.best_text, False, generation=generation, cancel_task=True)

        timer = threading.Timer(self.timeout, timed_out)
        timer.daemon = True
        timer.start()

    def cancel(self) -> None:
        with self.lock:
            self.generation += 1
            self.completed = True
            self.pending_final = None
            self._stop_audio()

    def _complete(self, text: str, reliable: bool, *, generation: int, cancel_task: bool = False) -> None:
        if self.completed or generation != self.generation:
            return
        self.completed = True
        self._stop_audio(cancel_task=cancel_task)
        self.final_callback(text.strip(), reliable, generation)

    def _stop_engine(self) -> None:
        if self.input_node is not None:
            try:
                self.input_node.removeTapOnBus_(0)
            except Exception:
                pass
            self.input_node = None
        if self.engine is not None:
            try:
                self.engine.stop()
            except Exception:
                pass
            self.engine = None

    def _stop_audio(self, *, cancel_task: bool = True) -> None:
        self._stop_engine()
        if self.request is not None:
            try:
                self.request.endAudio()
            except Exception:
                pass
            self.request = None
        if cancel_task and self.task is not None:
            try:
                self.task.cancel()
            except Exception:
                pass
        self.task = None
        self.recognizer = None

    @staticmethod
    def _level(buffer: Any) -> float:
        """Return a bounded RMS level from PyObjC's AudioBuffer memoryview."""
        try:
            audio_buffers = buffer.audioBufferList()
            data = audio_buffers[0].mData
            if data is None:
                return 0.0
            samples = data.cast("f")
            count = min(int(buffer.frameLength()), len(samples), 128)
            if count <= 0:
                return 0.0
            rms = math.sqrt(sum(float(samples[index]) ** 2 for index in range(count)) / count)
            return max(0.0, min(1.0, rms * 8))
        except (AttributeError, TypeError, ValueError):
            return 0.0
