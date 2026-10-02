"""Regressions for Chromium-version drift found while making the full suite green.

1. DOMSnapshot layout units: older Chromium reported device pixels, current
   builds report CSS pixels. Dividing by devicePixelRatio unconditionally put
   every target at half its real position on Retina displays.
2. Accessibility roles: <input type=date> is "date" (was "textbox"), and a
   file input is a "button".
3. Uploads have no snapshot predicate; the input's own read-back is the proof.
"""
from __future__ import annotations

from types import SimpleNamespace

from mcp_vision.guidance import overlay_function, overlay_script, resolve_target, role_matches
from mcp_vision.tasks import ContextTask, Step
from mcp_vision.verification import VerificationOutcome, VerificationResult
from phase2_mcp.cdp_snapshot import layout_scale


def test_layout_scale_is_measured_not_assumed():
    css_layout = [{"contentWidth": 1280}]
    device_layout = [{"contentWidth": 2560}]
    retina = {"deviceScaleFactor": 2, "cssContentWidth": 1280}
    assert layout_scale(css_layout, retina) == 1.0          # current Chromium
    assert layout_scale(device_layout, retina) == 2.0       # zoom-for-DSF builds
    assert layout_scale(css_layout, {"deviceScaleFactor": 1, "cssContentWidth": 1280}) == 1.0
    assert layout_scale([], {"deviceScaleFactor": 2}) == 2.0  # no calibration data: historical behavior
    assert layout_scale(css_layout, {"deviceScaleFactor": 0, "cssContentWidth": 1280}) == 1.0


def test_role_families_cover_native_input_drift():
    date = {"name": "Available start date", "role": "date", "input_type": "date", "w": 10, "h": 10}
    upload = {"name": "Résumé", "role": "button", "input_type": "file", "w": 10, "h": 10}
    submit = {"name": "Submit", "role": "button", "input_type": "submit", "w": 10, "h": 10}
    assert role_matches(date, "textbox") and role_matches(upload, "textbox")
    assert role_matches(upload, "button") and not role_matches(submit, "textbox")
    assert resolve_target([date, upload, submit], "available start date", "textbox") is date
    assert resolve_target([date, upload, submit], "Résumé", "textbox") is upload
    assert resolve_target([submit], "Submit", "textbox") is None


def test_upload_read_back_is_a_postcondition_only_when_it_matched():
    unknown = VerificationResult(outcome=VerificationOutcome.UNKNOWN, predicate="custom", state_id="s",
                                 message="none")
    after = SimpleNamespace(snapshot_id="s2")
    step = Step(action="upload", name="Résumé", role="textbox")
    good = SimpleNamespace(status="verified", evidence={"file_matches": True, "file_name": "resume.txt"})
    bad = SimpleNamespace(status="unverified", evidence={"file_matches": False, "file_name": "resume.txt"})
    result = ContextTask.upload_read_back(step, {"index": 6}, good, after, unknown)
    assert result.outcome is VerificationOutcome.SATISFIED and result.target == "@e6"
    assert ContextTask.upload_read_back(step, {"index": 6}, bad, after, unknown) is unknown
    fill = Step(action="fill", name="x", value="y")
    assert ContextTask.upload_read_back(fill, {"index": 1}, good, after, unknown) is unknown


def test_overlay_runs_against_an_element_or_a_tagged_index():
    function = overlay_function()
    assert function.startswith("function(LABEL_ARG, DURATION_ARG)") and "let target = this;" in function
    script = overlay_script(3, 'Say "hi"', 1500)
    assert '[data-agent-index="3"]' in script and '"Say \\"hi\\""' in script and ", 1500)" in script
