"""The permission card: the right System Settings page, docked under its window, done when the switch is on."""
from __future__ import annotations

from mcp_vision.buddy.permission_guide import (
    DRAGGABLE, PANEL, SIDEBAR, GuideFlow, appkit_frame, bundle_for, card_text, dock, pane_url, settings_window,
)


def test_each_permission_opens_its_own_page():
    assert pane_url("accessibility").endswith("?Privacy_Accessibility")
    assert pane_url("screen").endswith("?Privacy_ScreenCapture")
    assert pane_url("fulldisk").endswith("?Privacy_AllFiles")
    assert pane_url("nonsense").endswith("?Privacy")


def test_the_card_says_drag_where_the_list_takes_apps_and_switch_on_where_it_doesnt():
    assert {"accessibility", "screen"} <= DRAGGABLE and "microphone" not in DRAGGABLE
    title, hint = card_text("accessibility", "Plip")
    assert title == "Drag Plip into the list above" and "Already listed" in hint
    title, _ = card_text("microphone", "Terminal")
    assert title == "Switch on Terminal in the list above"


WINDOWS = [
    {"kCGWindowOwnerPID": 7, "kCGWindowLayer": 0, "kCGWindowBounds": {"X": 300, "Y": 120, "Width": 800, "Height": 600}},
    {"kCGWindowOwnerPID": 7, "kCGWindowLayer": 0, "kCGWindowBounds": {"X": 10, "Y": 10, "Width": 200, "Height": 90}},
    {"kCGWindowOwnerPID": 7, "kCGWindowLayer": 25, "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1512, "Height": 900}},
    {"kCGWindowOwnerPID": 9, "kCGWindowLayer": 0, "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1400, "Height": 900}},
]


def test_it_finds_system_settings_main_window_by_its_process():
    assert settings_window(WINDOWS, 7) == (300.0, 120.0, 800.0, 600.0)      # not a sheet, a menu, or another app
    assert settings_window(WINDOWS, 42) is None


def test_coregraphics_frames_flip_into_appkit():
    assert appkit_frame((300, 120, 800, 600), 982) == (300, 262, 800, 600)


def test_the_card_docks_under_the_content_inside_the_window_and_stays_on_screen():
    settings = (300.0, 262.0, 800.0, 600.0)
    visible = (0.0, 0.0, 1512.0, 950.0)
    x, y = dock(settings, visible)
    assert abs((x + PANEL[0] / 2) - (300 + SIDEBAR + (800 - SIDEBAR) / 2)) < 0.01    # centered on the content
    assert 262 - 8 <= y <= 262 + 4                                                  # at the window's bottom edge
    x, y = dock((1300.0, -40.0, 800.0, 600.0), visible)                             # half off screen
    assert x <= 1512 - PANEL[0] - 8 and y >= 8


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_the_flow_follows_settings_steps_aside_and_closes_once_its_on():
    clock = Clock()
    flow = GuideFlow("accessibility", clock=clock, linger=1.0)
    frame, visible = (300.0, 262.0, 800.0, 600.0), (0.0, 0.0, 1512.0, 950.0)
    assert flow.tick(granted=False, settings_front=False, frame=None, visible=None).hide       # still opening
    step = flow.tick(granted=False, settings_front=True, frame=frame, visible=visible)
    assert step.show == dock(frame, visible)
    assert flow.tick(granted=False, settings_front=False, frame=frame, visible=visible).hide  # they clicked away
    step = flow.tick(granted=True, settings_front=True, frame=frame, visible=visible)
    assert step.granted and flow.done and not step.close                                       # show the check first
    clock.now += 0.5
    assert not flow.tick(granted=True, settings_front=True, frame=frame, visible=visible).close
    clock.now += 0.6
    assert flow.tick(granted=True, settings_front=True, frame=frame, visible=visible).close


def test_the_flow_gives_up_after_a_while_without_a_grant():
    clock = Clock()
    flow = GuideFlow("screen", clock=clock, timeout=60)
    clock.now += 61
    step = flow.tick(granted=False, settings_front=True, frame=(0, 0, 900, 600), visible=(0, 0, 1512, 950))
    assert step.close and not flow.done


def test_closing_system_settings_without_switching_it_on_stands_the_card_down():
    clock = Clock()
    flow = GuideFlow("accessibility", clock=clock, gone=5.0)
    clock.now += 2                                   # still launching: give it a moment
    assert not flow.tick(granted=False, settings_front=False, frame=None, visible=None, settings_open=False).close
    flow.tick(granted=False, settings_front=True, frame=(0, 0, 900, 600), visible=(0, 0, 1512, 950))
    clock.now += 4
    assert not flow.tick(granted=False, settings_front=False, frame=None, visible=None, settings_open=False).close
    clock.now += 2
    step = flow.tick(granted=False, settings_front=False, frame=None, visible=None, settings_open=False)
    assert step.close and not flow.done


def test_the_app_to_drag_is_the_bundle_around_the_process():
    assert bundle_for("/Applications/Plip.app/Contents/MacOS/Plip") == "/Applications/Plip.app"
    assert bundle_for("/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal") == \
        "/System/Applications/Utilities/Terminal.app"
    assert bundle_for("/opt/homebrew/bin/python3.12") == ""                  # not in an app: nothing to drag
