"""Test 3.1: Destructive OS Action Interception (Governor & HUD Gatekeeper)."""

from mcp_vision.core.actuate import RecordingActuator, set_actuator
from mcp_vision.core.governor import Governor, classify, Policy
from mcp_vision.core.models import ScreenElement, BoundingBox
from mcp_vision.overlay.hud import set_forced_result
from mcp_vision.server import click_element, type_text, press_key_combination, inspect_image, reset_session, set_governor
from PIL import Image, ImageDraw


def test_destructive_terminal_rm_classification():
    # 1. Classify terminal and rm actions
    p_term = classify("open_app", text="Open Terminal and run rm -rf test_folder")
    assert p_term == Policy.RESTRICTED_ACTION, "Destructive terminal prompt must be RESTRICTED_ACTION"

    p_type = classify("type_text", text="rm -rf test_folder")
    assert p_type == Policy.RESTRICTED_ACTION, "rm -rf must be RESTRICTED_ACTION"

    p_sudo = classify("press_key_combination", keys=["sudo", "rm"])
    assert p_sudo == Policy.RESTRICTED_ACTION, "sudo rm key chord must be RESTRICTED_ACTION"

    p_safe = classify("inspect_screen")
    assert p_safe == Policy.SAFE_READ, "inspect_screen must be SAFE_READ"


def test_hud_esc_aborts_destructive_action():
    reset_session()
    act = RecordingActuator()
    set_actuator(act)

    # Simulate user pressing ESC in HUD (abort)
    set_forced_result(False)

    # Create dummy screen state with an element
    im = Image.new("RGB", (200, 200), (255, 255, 255))
    draw = ImageDraw.Draw(im)
    draw.rectangle([20, 20, 100, 60], fill=(0, 0, 0))
    res = inspect_image(im, display_id=0, scale=1.0)
    
    import mcp_vision.server as srv
    srv._last = res
    from mcp_vision.core.capture import Frame
    srv._last_frame = Frame(display_id=0, image=im, png=b"", width=200, height=200, scale=1.0, monitor={"left": 0, "top": 0, "width": 200, "height": 200})

    # Attempt destructive type action
    result = type_text(0, "rm -rf test_folder", press_enter=True)
    assert not result.ok, "Action must be blocked when HUD Esc is pressed"
    assert result.message == "blocked by safety governor"
    assert result.confirmed is False
    assert result.policy == Policy.RESTRICTED_ACTION
    assert len(act.calls) == 0, "No keystrokes must be emitted to OS when aborted"


def test_hud_space_allows_destructive_action():
    reset_session()
    act = RecordingActuator()
    set_actuator(act)

    # Simulate user pressing SPACE/ENTER in HUD (confirm)
    set_forced_result(True)

    im = Image.new("RGB", (200, 200), (255, 255, 255))
    draw = ImageDraw.Draw(im)
    draw.rectangle([20, 20, 100, 60], fill=(0, 0, 0))
    res = inspect_image(im, display_id=0, scale=1.0)
    
    import mcp_vision.server as srv
    srv._last = res
    from mcp_vision.core.capture import Frame
    srv._last_frame = Frame(display_id=0, image=im, png=b"", width=200, height=200, scale=1.0, monitor={"left": 0, "top": 0, "width": 200, "height": 200})

    result = type_text(0, "rm -rf test_folder", press_enter=True)
    assert result.ok is True, "Action must proceed when HUD Space is pressed"
    assert result.policy == Policy.RESTRICTED_ACTION
    assert len(act.calls) == 2, "Click and typing must be executed upon confirmation"


if __name__ == "__main__":
    test_destructive_terminal_rm_classification()
    test_hud_esc_aborts_destructive_action()
    test_hud_space_allows_destructive_action()
    print("Test 3.1 passed successfully!")
