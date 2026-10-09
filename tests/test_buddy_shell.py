"""Platform-neutral parts of the macOS shell: hotkey chord, speech input, controller flow."""
from __future__ import annotations

import array
import asyncio
import json
import queue
import threading
import time
import urllib.parse

import pytest

from mcp_vision.buddy.companion import TurnResult
from mcp_vision.buddy.controller import BuddyController
from mcp_vision.buddy.hotkey import COMMAND, CONTROL, OPTION, SHIFT, ChordDetector
from mcp_vision.buddy.overlay_macos import wave_heights
from mcp_vision.buddy.speech_in import (
    AssemblyAIListener, AssemblyAISession, ListenerCallbacks, TurnAssembler, assemblyai_url, pcm16_level,
)


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


# -- hotkey ----------------------------------------------------------------------

def detector():
    events = []
    chord = ChordDetector(on_press=lambda: events.append("press"), on_release=lambda: events.append("release"),
                          on_cancel=lambda: events.append("cancel"))
    return chord, events


def test_chord_press_and_release_on_either_modifier():
    chord, events = detector()
    chord.flags_changed(CONTROL)
    chord.flags_changed(CONTROL | OPTION)
    chord.flags_changed(CONTROL | OPTION | SHIFT)     # still held
    chord.flags_changed(OPTION)                        # control let go
    chord.flags_changed(0)
    assert events == ["press", "release"]


def test_chord_with_command_is_not_ours_and_key_during_chord_cancels():
    chord, events = detector()
    chord.flags_changed(CONTROL | OPTION | COMMAND)
    assert events == []
    chord.flags_changed(CONTROL | OPTION)
    chord.key_down()                                    # ctrl+opt+arrow in some app
    chord.key_down()
    chord.flags_changed(0)
    assert events == ["press", "cancel"]
    chord.key_down()                                    # keys outside the chord are ignored
    assert events == ["press", "cancel"]
    chord.flags_changed(CONTROL | OPTION)              # ⌃⌥ then ⌘ (Rectangle's ⌃⌥⌘→): not ours
    chord.flags_changed(CONTROL | OPTION | COMMAND)
    chord.flags_changed(CONTROL | OPTION)
    chord.flags_changed(0)
    assert events == ["press", "cancel", "press", "cancel"]


# -- speech in ---------------------------------------------------------------------

def test_turn_assembler_matches_clicky_rules():
    turns = TurnAssembler()
    assert turns.add({"transcript": "where is", "turn_order": 0}) == ("where is", False)
    assert turns.add({"transcript": "where is the", "turn_order": 0}) == ("where is the", False)
    assert turns.add({"transcript": "where is the export", "turn_order": 0, "end_of_turn": True}) == (
        "where is the export", True)
    turns.add({"transcript": "Where is the export?", "turn_order": 0, "end_of_turn": True,
               "turn_is_formatted": True})
    turns.add({"transcript": "where is the export", "turn_order": 0, "end_of_turn": True})  # never downgrades
    turns.add({"transcript": "button", "turn_order": 1})
    assert turns.text == "Where is the export? button"


def test_assemblyai_url_has_clicky_parameters():
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(assemblyai_url("tok", ["b", "A", "a"])).query))
    assert query["sample_rate"] == "16000" and query["encoding"] == "pcm_s16le"
    assert query["format_turns"] == "true" and query["speech_model"] == "u3-rt-pro"
    assert query["token"] == "tok"
    assert json.loads(query["keyterms_prompt"])[0].lower() == "a"


def test_pcm16_level_is_bounded():
    silence = array.array("h", [0] * 512).tobytes()
    loud = array.array("h", [20000, -20000] * 256).tobytes()
    assert pcm16_level(silence) == 0.0 and pcm16_level(b"") == 0.0
    assert pcm16_level(loud) == 1.0


class FakeSocket:
    """A websockets-like connection driven by a queue of server messages."""

    def __init__(self):
        self.incoming: queue.Queue = queue.Queue()
        self.sent = []
        self.closed = False

    def __iter__(self):
        while True:
            item = self.incoming.get()
            if item is None:
                return
            yield item

    def send(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True
        self.incoming.put(None)

    def server(self, **message):
        self.incoming.put(json.dumps(message))


def session_with(socket, grace=0.2):
    finals, partials, errors = [], [], []
    callbacks = ListenerCallbacks(partial=partials.append, final=finals.append, error=errors.append)
    session = AssemblyAISession("wss://x", callbacks, connect=lambda url: socket, grace=grace)
    return session, finals, partials, errors


def test_session_waits_for_begin_streams_audio_and_delivers_on_end_of_turn():
    socket = FakeSocket()
    session, finals, partials, _ = session_with(socket)
    socket.server(type="Begin", id="s1")
    session.open(timeout=1)
    session.send_audio(b"\x00\x01")
    socket.server(type="Turn", transcript="hello the", turn_order=0)
    session.request_final()
    socket.server(type="Turn", transcript="Hello there.", turn_order=0, end_of_turn=True, turn_is_formatted=True)
    assert wait_for(lambda: finals)
    assert finals == ["Hello there."] and partials[0] == "hello the"
    assert socket.sent[0] == b"\x00\x01"
    sent_json = [json.loads(m)["type"] for m in socket.sent if isinstance(m, str)]
    assert sent_json == ["ForceEndpoint", "Terminate"]
    assert wait_for(lambda: socket.closed)


def test_session_grace_period_delivers_best_text_without_end_of_turn():
    socket = FakeSocket()
    session, finals, _, _ = session_with(socket, grace=0.1)
    socket.server(type="Begin")
    session.open(timeout=1)
    socket.server(type="Turn", transcript="open settings", turn_order=0)
    assert wait_for(lambda: session.assembler.text == "open settings")
    session.request_final()
    assert wait_for(lambda: finals, timeout=1)
    assert finals == ["open settings"]


def test_session_error_after_release_salvages_partial_else_reports():
    socket = FakeSocket()
    session, finals, _, errors = session_with(socket, grace=5)
    socket.server(type="Begin")
    session.open(timeout=1)
    socket.server(type="Turn", transcript="where's the", turn_order=0)
    assert wait_for(lambda: session.assembler.text)
    session.request_final()
    socket.server(type="Error", error="boom")
    assert wait_for(lambda: finals) and finals == ["where's the"] and errors == []

    socket = FakeSocket()
    session, finals, _, errors = session_with(socket)
    socket.server(type="Begin")
    session.open(timeout=1)
    socket.server(type="Error", error="bad token")
    assert wait_for(lambda: errors) and "bad token" in errors[0] and finals == []


def test_listener_buffers_audio_until_connected_and_finalizes_after_early_release():
    socket = FakeSocket()
    finals = []
    mic_holder = {}

    class FakeMic:
        def __init__(self, on_audio):
            mic_holder["push"] = on_audio
            self.stopped = False

        def start(self):
            pass

        def stop(self):
            self.stopped = True

    gate = threading.Event()

    def slow_session(url):
        gate.wait(1)
        return AssemblyAISession(url, ListenerCallbacks(final=finals.append), connect=lambda u: socket, grace=0.1)

    listener = AssemblyAIListener("key", ListenerCallbacks(final=finals.append),
                                  token_source=lambda: "tok", session_factory=slow_session, mic_factory=FakeMic)
    listener.start()
    mic_holder["push"](b"early-audio")
    listener.release()                                  # user let go before the socket opened
    socket.server(type="Begin")
    gate.set()
    assert wait_for(lambda: any(m == b"early-audio" for m in socket.sent))
    assert wait_for(lambda: any(isinstance(m, str) and "ForceEndpoint" in m for m in socket.sent))
    socket.server(type="Turn", transcript="hi", turn_order=0, end_of_turn=True)
    assert wait_for(lambda: finals) and finals == ["hi"]


def test_wave_heights_respond_to_level():
    quiet, loud = wave_heights(0.0, 0.0), wave_heights(1.0, 0.0)
    assert len(quiet) == 5 and all(3 <= h <= 4.6 for h in quiet)
    assert loud[2] > loud[0] > quiet[0] and loud[2] == pytest.approx(quiet[2] + 10)


# -- controller --------------------------------------------------------------------

class FakeOverlay:
    def __init__(self):
        self.states, self.levels = [], []

    def set_state(self, state, detail=""):
        self.states.append(state)

    def set_level(self, level):
        self.levels.append(level)


class FakeListener:
    def __init__(self):
        self.calls = []

    def start(self):
        self.calls.append("start")

    def release(self):
        self.calls.append("release")

    def cancel(self):
        self.calls.append("cancel")


class FakeCompanion:
    def __init__(self, delay=0.0):
        self.delay = delay
        self.asked, self.interrupts, self.prefetches = [], 0, 0

    def interrupt(self, token=None):
        self.interrupts += 1
        self.token = token

    def prefetch(self):
        self.prefetches += 1

    async def respond(self, text, token=None):
        self.asked.append(text)
        self.tokens = getattr(self, "tokens", []) + [token]
        await asyncio.sleep(self.delay)
        return TurnResult(transcript=text, spoken="ok", timings={"first_speech": 900.0})


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(1)


def make_controller(loop, companion=None, **kw):
    timers, main_calls, statuses, said = [], [], [], []
    controller = BuddyController(
        companion=companion or FakeCompanion(), overlay=FakeOverlay(), loop=loop, listener=FakeListener(),
        call_later=lambda delay, fn: timers.append((delay, fn)),
        on_main=lambda fn, *args: main_calls.append((fn, args)),
        status=statuses.append, say=said.append, **kw)
    return controller, timers, main_calls, statuses, said


def run_main(main_calls):
    while main_calls:
        fn, args = main_calls.pop(0)
        fn(*args)


def test_full_push_to_talk_turn(loop):
    controller, timers, main_calls, statuses, _ = make_controller(loop)
    controller.on_press()
    controller.on_level(0.5)
    controller.on_release()
    controller.on_final("  where is   export ")
    assert wait_for(lambda: main_calls)
    run_main(main_calls)
    assert controller.companion.asked == ["where is export"]
    assert controller.companion.prefetches == 1
    assert controller.listener.calls == ["start", "release"]
    assert controller.overlay.states == ["listening", "thinking"] and controller.overlay.levels == [0.5]
    assert controller.state == "idle" and statuses[-1] == "Ready - answered in 0.9s"
    assert wait_for(lambda: controller.companion.interrupts == 1)


def test_quick_tap_or_silence_goes_idle_without_asking(loop):
    controller, timers, *_ = make_controller(loop)
    controller.on_press()
    controller.on_release()
    controller.on_final("   ")
    assert controller.state == "idle" and controller.companion.asked == []
    assert controller.overlay.states[-1] == "idle"
    controller.on_final("late text")                    # stale transcript ignored
    assert controller.companion.asked == []


def test_transcript_timeout_and_cancel_paths(loop):
    controller, timers, *_ = make_controller(loop)
    controller.on_press()
    controller.on_release()
    delay, fire = timers[0]
    assert delay == BuddyController.FINAL_TIMEOUT
    fire()
    assert controller.state == "idle" and controller.listener.calls[-1] == "cancel"

    controller.on_press()
    controller.on_cancel()
    assert controller.state == "idle" and controller.listener.calls[-1] == "cancel"


def test_barge_in_ignores_the_interrupted_answer(loop):
    controller, timers, main_calls, statuses, _ = make_controller(loop, FakeCompanion(delay=0.2))
    controller.on_press()
    controller.on_release()
    controller.on_final("first question")
    controller.on_press()                               # talk over the buddy
    assert controller.state == "listening"
    assert wait_for(lambda: main_calls, timeout=2)
    run_main(main_calls)                                # old turn finishing must not reset state
    assert controller.state == "listening"


def test_macos_modules_import_without_appkit():
    import importlib

    for name in ("app_macos", "overlay_macos", "hotkey", "ax_locator", "speech_in"):
        importlib.import_module(f"mcp_vision.buddy.{name}")
    from mcp_vision.buddy.ax_locator import elements_near, probe_points

    assert len(probe_points(10, 10, 90)) == 17
    found = elements_near(10, 10, 90)               # no crash either way
    try:
        import ApplicationServices as AX
        trusted = bool(AX.AXIsProcessTrusted())
    except ImportError:
        trusted = False
    if not trusted:
        assert found == []                          # no accessibility API here: nothing
    else:
        assert all(e.bounds.width >= 0 for e in found)   # a mac that granted it sees the real menu bar


def test_setup_error_speaks_instead_of_listening(loop):
    controller, _, _, statuses, said = make_controller(loop, setup_error="ANTHROPIC_API_KEY is not set.")
    controller.on_press()
    assert controller.listener.calls == [] and said and "Needs setup" in statuses[-1]


def test_recognition_error_stops_the_microphone(loop):
    controller, *_ = make_controller(loop)
    controller.on_press()
    controller.on_error("speech recognition failed: socket closed")
    assert controller.state == "idle" and controller.listener.calls == ["start", "cancel"]


def test_backlog_is_flushed_in_order_before_live_audio():
    socket = FakeSocket()
    pushed = {}

    class FakeMic:
        def __init__(self, on_audio):
            pushed["push"] = on_audio

        def start(self):
            pass

        def stop(self):
            pass

    gate = threading.Event()

    class SlowSession(AssemblyAISession):
        def send_audio(self, chunk):
            if chunk == b"a1":
                pushed["push"](b"live")       # audio arrives mid-flush
            super().send_audio(chunk)

    def factory(url):
        gate.wait(1)
        return SlowSession(url, ListenerCallbacks(), connect=lambda u: socket)

    listener = AssemblyAIListener("key", ListenerCallbacks(), token_source=lambda: "tok",
                                  session_factory=factory, mic_factory=FakeMic)
    listener.start()
    pushed["push"](b"a1")
    pushed["push"](b"a2")
    socket.server(type="Begin")
    gate.set()
    assert wait_for(lambda: len([m for m in socket.sent if isinstance(m, bytes)]) == 3)
    assert [m for m in socket.sent if isinstance(m, bytes)] == [b"a1", b"a2", b"live"]


def test_every_offered_shortcut_presses_releases_and_ignores_other_apps_shortcuts():
    from mcp_vision.buddy.hotkey import CHORDS, chord

    assert [chord(name).symbols for name in CHORDS] == ["⌃⌥", "⌥⌘", "⌃⇧", "⌃⌘"]
    assert chord("option+command").label == "Option + Command" and chord("hyper+f13").id == "control+option"
    for name in CHORDS:
        picked = chord(name)
        chord_, events = detector()
        chord_.set_chord(picked.mask)
        chord_.flags_changed(picked.mask | SHIFT if not picked.mask & SHIFT else picked.mask)   # a stray Shift is fine
        chord_.flags_changed(0)
        assert events == ["press", "release"], name
        for extra in (CONTROL, OPTION, COMMAND):           # an extra modifier: another app's shortcut
            if not picked.mask & extra:
                chord_.flags_changed(picked.mask | extra)
                chord_.flags_changed(0)
        assert events == ["press", "release"], name
    # old default stops answering
    chord_, events = detector()
    chord_.set_chord(chord("option+command").mask)
    chord_.flags_changed(CONTROL | OPTION)
    assert events == []


def test_changing_the_shortcut_mid_press_drops_that_press():
    from mcp_vision.buddy.hotkey import chord

    chord_, events = detector()
    chord_.flags_changed(CONTROL | OPTION)
    chord_.set_chord(chord("control+shift").mask)
    chord_.flags_changed(0)
    assert events == ["press", "cancel"]                # never sent half a question


def test_the_listener_only_says_it_works_when_macos_passes_it_keys():
    from mcp_vision.buddy.hotkey import MacHotkeyListener

    class Listener(MacHotkeyListener):
        """Real logic minus the Quartz tap (a real one would hear this Mac's keys)."""

        made = 0

        def _start_tap(self):
            self.made += 1
            return True

        def _start_monitors(self):
            return False

        def stop(self):
            self.mechanism = "none"

    allowed = [False]
    listener = Listener(ChordDetector(on_press=lambda: None, on_release=lambda: None, on_cancel=lambda: None),
                        allowed=lambda: allowed[0])
    # no Accessibility: the tap is made but never gets keys
    assert listener.start() == "none" and listener.made == 1
    assert listener.mode() == "none"
    allowed[0] = True                                   # turned on: re-listen (old tap stays deaf)
    assert listener.mode() == "event-tap" and listener.made == 2
    assert listener.mode() == "event-tap" and listener.made == 2


def test_it_names_whose_accessibility_the_shortcut_needs(monkeypatch):
    from mcp_vision.buddy import hotkey

    monkeypatch.setattr(hotkey.sys, "prefix", "/Applications/Plip.app/Contents/Resources/python")
    assert hotkey.keyboard_owner() == "Plip"
    monkeypatch.setattr(hotkey.sys, "prefix", "/Users/me/plip/.venv")
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    assert hotkey.keyboard_owner() == "Terminal"
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    assert hotkey.keyboard_owner() == "iTerm"
