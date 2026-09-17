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


def test_input_supports_standard_editing_without_system_focus_ring():
    src = _source()
    assert 'self.input.setMenu_(_edit_menu())' in src
    assert '"copy:", "c"' in src and '"paste:", "v"' in src
    assert 'self.input.setFocusRingType_(AppKit.NSFocusRingTypeNone)' in src
    assert 'self.input.setBezeled_(False)' in src


def test_popup_names_the_control_under_the_cursor():
    assert 'under cursor:' in _source()
