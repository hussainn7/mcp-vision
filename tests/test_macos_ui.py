"""Regression guards for the macOS contextual popup wiring.

Constructing the AppKit UI needs a live macOS session, so these test the module
*source* for the launch-critical glue that has regressed before (install_hotkey
was deleted while the launcher still called it, crashing `mcp-vision ui` on
startup).
"""
from __future__ import annotations

from importlib import resources

import mcp_vision.macos_ui as ui


def _source() -> str:
    return resources.files("mcp_vision").joinpath("macos_ui.py").read_text()


def test_install_hotkey_is_defined_and_called():
    src = _source()
    # The method must exist (it was deleted once) and the launcher must still
    # call it, or the UI raises AttributeError on startup.
    assert "def install_hotkey" in src
    assert "install_hotkey()" in src


def test_install_hotkey_wires_both_hotkey_paths():
    src = _source()
    # Carbon-native (packaged launcher) posts a distributed notification the
    # handler observes; the CLI path sets up its own AppKit key monitors.
    assert "nativeHotkey:" in src
    assert '"org.mcpvision.contextual.hotkey"' in src
    assert "addGlobalMonitorForEventsMatchingMask_handler_" in src
    assert "addLocalMonitorForEventsMatchingMask_handler_" in src


def test_status_item_and_menu_invoke_are_present():
    src = _source()
    assert "def _install_status_item" in src
    assert "_menu_invoke" in src
    # Show/hide indicator helpers used by the acting flow.
    assert "def hide_indicator" in src
    assert "def show_indicator" in src


def test_capture_and_submission_helpers_exist():
    import inspect

    assert callable(ui.capture_native_context)
    assert callable(ui.submission_context)
    # The submission helper keeps the popup's own fields out of task evidence.
    assert inspect.isroutine(ui.submission_context) or callable(ui.submission_context)


def test_native_command_starts_fresh_instead_of_joining_research_clarification():
    from mcp_vision.context import Context

    context = ui.submission_context(
        Context(source="macos"),
        "Please just open the Notes app for me",
        "Find flights to San Francisco next week",
    )
    assert context.user_request == "Please just open the Notes app for me"


def test_generic_ui_action_starts_fresh_instead_of_joining_research_clarification():
    from mcp_vision.context import Context

    context = ui.submission_context(
        Context(source="macos", source_application="Notes"),
        "Are you able to create a new note?",
        "Find flights to San Francisco next week",
    )
    assert context.user_request == "Are you able to create a new note?"


def test_input_supports_standard_editing_without_system_focus_ring():
    src = _source()
    assert 'self.input.setMenu_(_edit_menu())' in src
    assert '"copy:", "c"' in src and '"paste:", "v"' in src
    assert 'self.input.setFocusRingType_(AppKit.NSFocusRingTypeNone)' in src
    assert 'self.input.setBezeled_(False)' in src


def test_popup_names_the_control_under_the_cursor():
    assert 'under cursor:' in _source()


def test_hold_to_talk_and_compact_activity_panel_are_wired():
    src = _source()
    assert "NSEventMaskKeyUp" in src
    assert "HoldToTalk" in src and "AppleSpeechSession" in src
    assert "NSWindowStyleMaskNonactivatingPanel" in src
    assert '"listening": "LISTENING"' in src
    assert '"verifying": "VERIFYING"' in src


def test_top_center_geometry_handles_offset_displays():
    assert ui.top_center_origin((100, -900, 1200, 800), (400, 88)) == (500, -198)


def test_launcher_declares_voice_permissions_and_release_event():
    from pathlib import Path

    src = Path("scripts/build_macos_app.py").read_text()
    assert "NSMicrophoneUsageDescription" in src
    assert "NSSpeechRecognitionUsageDescription" in src
    assert "kEventHotKeyReleased" in src
    assert "source.encode() + plist_data" in src
    assert "stamp_name" in src
    assert "--no-install" in src


def test_launcher_builds_icon_and_uses_stable_designated_requirement():
    from pathlib import Path

    src = Path("scripts/build_macos_app.py").read_text()
    assert "dist' / 'logoW.png" in src
    assert "CFBundleIconFile" in src and "iconutil" in src
    assert 'designated => identifier "org.mcpvision.contextual"' in src
