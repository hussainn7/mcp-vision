from mcp_vision.macos_input import targeted_key, targeted_replace_text


class Quartz:
    kCGEventFlagMaskCommand = 1 << 20
    posted = []

    @staticmethod
    def CGEventCreateKeyboardEvent(_source, keycode, down):
        return {"keycode": keycode, "down": down, "flags": 0, "text": ""}

    @staticmethod
    def CGEventSetFlags(event, flags):
        event["flags"] = flags

    @staticmethod
    def CGEventKeyboardSetUnicodeString(event, _length, text):
        event["text"] = text

    @classmethod
    def CGEventPostToPid(cls, pid, event):
        cls.posted.append((pid, dict(event)))


def test_pid_targeted_text_never_uses_global_event_post(monkeypatch):
    Quartz.posted = []
    monkeypatch.setitem(__import__('sys').modules, 'Quartz', Quartz)
    monkeypatch.setattr('mcp_vision.macos_input.time.sleep', lambda _: None)
    assert targeted_replace_text(123, "hello")
    assert all(pid == 123 for pid, _event in Quartz.posted)
    assert any(event["flags"] == Quartz.kCGEventFlagMaskCommand for _pid, event in Quartz.posted)
    assert "".join(event["text"] for _pid, event in Quartz.posted if event["down"]) == "hello"


def test_pid_targeted_key_uses_requested_process(monkeypatch):
    Quartz.posted = []
    monkeypatch.setitem(__import__('sys').modules, 'Quartz', Quartz)
    assert targeted_key(77, 121)
    assert [(pid, event["keycode"], event["down"]) for pid, event in Quartz.posted] == [
        (77, 121, True), (77, 121, False),
    ]
