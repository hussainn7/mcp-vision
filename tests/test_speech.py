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


def test_the_system_voice_speaks_in_process_stops_on_the_spot_and_falls_back_to_say():
    import threading

    from mcp_vision.buddy.speech_out import SystemVoice

    class Synth:
        def __init__(self, ok=True, words=3):
            self.ok, self.words, self.rate, self.said, self.stopped = ok, words, None, [], False

        def setRate_(self, rate):
            self.rate = rate

        def startSpeakingString_(self, text):
            self.said.append(text)
            return self.ok

        def isSpeaking(self):
            self.words -= 1
            return self.words > 0 and not self.stopped

        def stopSpeaking(self):
            self.stopped = True

    synth, ran = Synth(), []
    voice = SystemVoice(synthesizer=lambda: synth, runner=lambda command, stop: ran.append(command))
    voice.play("Opening Spotify.", threading.Event())
    assert synth.said == ["Opening Spotify."] and synth.rate == 200.0 and ran == []      # no `say` process
    stop = threading.Event()
    stop.set()
    synth.words = 3
    voice.play("long answer", stop)
    assert synth.stopped                                                                 # cut off right away
    broken = SystemVoice(synthesizer=lambda: Synth(ok=False), runner=lambda command, stop: ran.append(command))
    broken.play("still heard", threading.Event())
    assert ran == [["say", "-r", "200", "--", "still heard"]]                            # falls back to say
