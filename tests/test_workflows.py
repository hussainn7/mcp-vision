from mcp_vision.browser import BrowserSnapshot
from mcp_vision.state import compile_state
from mcp_vision.workflows import WorkflowLibrary, learn_workflow, resolve_step


def replay(verified=True):
    return {
        "states": [{"state_id": "s1", "root_id": "root", "epoch": 1, "source": "dom-accessibility",
                    "observed_at": 1, "url": "https://fixture.test/settings", "title": "Settings",
                    "text": "Open preferences", "content_hash": "h", "candidates": [], "pruned": {},
                    "elements": [{"ref": "@e0", "state_id": "s1", "root_id": "root", "role": "button",
                                  "name": "Open preferences", "value": "", "description": "",
                                  "bounds": {"x": 1, "y": 1, "w": 20, "h": 10},
                                  "identity": {"dom": "button#open", "accessibility": None, "visual": None},
                                  "capabilities": ["press"], "risk": "ROUTINE_WRITE", "sources": ["dom-accessibility"]}] }],
        "events": [
            {"type": "fastpath_start", "subgoal": "Open preferences",
             "completion": {"kind": "text_contains", "expected": "Preferences"}},
            {"type": "candidate", "state_id": "s1", "operation": "press", "risk": "ROUTINE_WRITE",
             "selected": {"id": "A1", "operation": "press", "target_ref": "@e0",
                          "label": "PRESS @e0 Open preferences", "risk": "ROUTINE_WRITE"}},
            {"type": "verification", "passed": verified, "predicate": "text_contains"},
            {"type": "fastpath_end", "status": "verified" if verified else "replan"},
        ],
    }


def live(identity="button#open", *, submits=False):
    return compile_state(BrowserSnapshot(
        snapshot_id="live", root_id="root", url="https://fixture.test/settings", title="Settings", text="",
        elements=[{"index": 0, "role": "button", "name": "Open preferences", "value": "",
                   "x": 5, "y": 5, "w": 100, "h": 25, "input_type": "button", "submits": submits,
                   "identity": {"dom": identity}}]), epoch=2)


def test_only_verified_runs_learn_and_changed_identity_repairs_semantically(tmp_path):
    assert learn_workflow(replay(False)) is None
    workflow = learn_workflow(replay())
    assert workflow and workflow.steps[0].target.name == "Open preferences"
    assert "x" not in workflow.steps[0].target.model_dump()  # never stores coordinates

    exact = resolve_step(workflow, 0, live())
    assert exact.status == "matched" and not exact.semantic_repair
    repaired = resolve_step(workflow, 0, live("button[data-action=prefs]"))
    assert repaired.status == "matched" and repaired.semantic_repair
    blocked = resolve_step(workflow, 0, live("changed", submits=True))
    assert blocked.status == "blocked"

    library = WorkflowLibrary(tmp_path / "workflows.json")
    library.add(workflow)
    assert library.load() == [workflow]


def test_workflow_refuses_wrong_site():
    workflow = learn_workflow(replay())
    state = live().model_copy(update={"url": "https://other.test/settings"})
    assert resolve_step(workflow, 0, state).status == "replan"
