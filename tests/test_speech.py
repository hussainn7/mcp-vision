from mcp_vision.speech import AppleSpeechSession


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
