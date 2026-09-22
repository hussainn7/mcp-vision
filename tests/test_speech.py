from mcp_vision.speech import AppleSpeechSession, HoldToTalk


def coordinator():
    scheduled = []
    events = []
    hold = HoldToTalk(lambda delay, callback: scheduled.append((delay, callback)), threshold=.2,
                      on_tap=lambda context: events.append(("tap", context)),
                      on_hold=lambda context: events.append(("hold", context)),
                      on_release=lambda: events.append(("release", None)))
    return hold, scheduled, events


def test_quick_tap_opens_typed_flow_only():
    hold, scheduled, events = coordinator()
    assert hold.press("context") is True
    assert hold.release() is True
    scheduled[0][1]()
    assert events == [("tap", "context")]
    assert hold.state == "idle"


def test_hold_starts_and_release_finalizes_once():
    hold, scheduled, events = coordinator()
    assert hold.press("context") is True
    assert hold.press("duplicate") is False
    scheduled[0][1]()
    scheduled[0][1]()
    assert hold.release() is True
    assert hold.release() is False
    assert events == [("hold", "context"), ("release", None)]
    assert hold.state == "finalizing"
    hold.finish()
    assert hold.state == "idle"


def test_cancel_invalidates_delayed_hold():
    hold, scheduled, events = coordinator()
    hold.press("context")
    hold.cancel()
    scheduled[0][1]()
    assert events == []


def test_stale_speech_completion_is_ignored():
    finals = []
    speech = AppleSpeechSession(partial=lambda _text, _generation: None,
                                final=lambda text, reliable, generation: finals.append((text, reliable, generation)),
                                level=lambda _level, _generation: None,
                                status=lambda _message, _generation: None)
    speech.generation = 4
    speech.completed = False
    speech._complete("old", True, generation=3)
    assert finals == []
    speech._complete("current", True, generation=4)
    assert finals == [("current", True, 4)]
