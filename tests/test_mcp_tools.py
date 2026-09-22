from __future__ import annotations

import json

from PIL import Image, ImageDraw

from mcp_vision.core.actuate import RecordingActuator, get_actuator
from mcp_vision.core.models import ActionResult, ScreenInspectionResult
from mcp_vision.overlay.hud import set_forced_result
from mcp_vision.server import (
    _mcp,
    click_element,
    inspect_screen,
    open_application,
    prepare_flight_search,
    press_key_combination,
    set_grabber,
    type_text,
)


def test_state_runtime_tools_publish_valid_mcp_schemas() -> None:
    import asyncio

    async def check():
        tools = await _mcp(fast_policy="rules").list_tools()
        names = {tool.name for tool in tools}
        expected = {
            "browser_observe", "browser_choose_candidate",
            "browser_execute_candidate", "browser_transaction_log", "browser_fastpath",
            "open_application", "prepare_flight_search",
        }
        assert expected <= names
        execute = next(tool for tool in tools if tool.name == "browser_execute_candidate")
        assert {"state_id", "candidate_id", "expected_kind"} <= set(execute.parameters["properties"])

    asyncio.run(check())


def test_open_application_uses_verified_native_action(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("mcp_vision.native_apps.perform", lambda action, value: calls.append((action, value)) or {
        "ok": True, "verified": True, "message": "Opened Notes.",
    })
    result = open_application("Notes")
    assert result["verified"] is True
    assert calls == [("open_app", "Notes")]


def test_prepare_flight_search_clarifies_then_returns_url() -> None:
    missing = prepare_flight_search("find me a flight to SF")
    assert missing["ready"] is False and missing["missing"] == "departure"
    ready = prepare_flight_search("find flights from ATL to SF next week")
    assert ready["ready"] is True
    assert ready["url"].startswith("https://www.google.com/travel/flights?")


def _ui() -> Image.Image:
    img = Image.new("RGB", (400, 200), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 90, 55], fill=(30, 30, 30))
    return img


def test_inspect_then_click_records_coords() -> None:
    set_forced_result(True)
    set_grabber(lambda _i: _ui())
    r = inspect_screen(0)
    assert r.elements
    act = get_actuator()
    assert isinstance(act, RecordingActuator)
    out = click_element(0)
    assert out.ok
    assert act.calls[0][0] == "click"
    x, y, kind = act.calls[0][1]
    assert kind == "single" and x > 0 and y > 0


def test_type_and_hotkey() -> None:
    set_forced_result(True)
    set_grabber(lambda _i: _ui())
    inspect_screen(0)
    assert type_text(0, "hello", press_enter=True).ok
    assert press_key_combination(["enter"]).ok
    act = get_actuator()
    assert isinstance(act, RecordingActuator)
    kinds = [c[0] for c in act.calls]
    assert "type" in kinds and "press" in kinds


def test_jsonrpc_result_serializes_without_png() -> None:
    set_grabber(lambda _i: _ui())
    r = inspect_screen(0)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": r.model_dump(),
    }
    raw = json.dumps(payload)
    data = json.loads(raw)
    assert data["result"]["width"] == 400
    assert "png" not in data["result"]
    assert data["result"]["elements"][0]["id"] == 0


def test_tool_schemas_cover_required_fields() -> None:
    ins = ScreenInspectionResult.model_json_schema()
    assert set(ins["properties"]) >= {"display_id", "width", "height", "elements"}
    act = ActionResult.model_json_schema()
    assert set(act["properties"]) >= {"ok", "message", "policy", "confirmed"}


def test_restricted_click_aborts_when_hud_says_no() -> None:
    img = Image.new("RGB", (200, 80), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 70, 40], fill=(20, 20, 20))
    set_grabber(lambda _i: img)
    r = inspect_screen(0)
    assert r.elements
    r.elements[0].label = "Delete forever"
    set_forced_result(False)
    out = click_element(0)
    assert not out.ok and out.confirmed is False
    act = get_actuator()
    assert isinstance(act, RecordingActuator)
    assert act.calls == []


def test_desktop_input_consumes_observation():
    set_forced_result(True)
    set_grabber(lambda _: _ui())
    inspect_screen()
    assert click_element(0).ok
    assert not click_element(0).ok
    assert len(get_actuator().calls) == 1


def test_desktop_screen_change_during_confirmation_blocks_input():
    from mcp_vision.server import set_governor
    from mcp_vision.core.governor import Governor
    current = [_ui()]
    set_grabber(lambda _: current[0])
    inspect_screen()
    def approve(*_):
        current[0] = Image.new('RGB', (400, 200), 'red')
        return True
    set_governor(Governor(confirmer=approve))
    assert not click_element(0).ok
    assert get_actuator().calls == []
