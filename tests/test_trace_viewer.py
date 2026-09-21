from trace_viewer import render_replay


def test_replay_viewer_explains_candidate_and_before_after():
    bundle = {
        "schema": 1,
        "states": [
            {"state_id": "s1", "epoch": 1, "root_id": "root", "url": "https://fixture.test",
             "title": "Fixture", "text": "Waiting", "elements": [
                 {"ref": "@e1", "role": "button", "name": "Continue"}]},
            {"state_id": "s2", "epoch": 2, "root_id": "root", "url": "https://fixture.test",
             "title": "Fixture", "text": "Done", "elements": []},
        ],
        "events": [
            {"ts": 1.0, "type": "fastpath_start", "subgoal": "Press Continue"},
            {"ts": 1.1, "type": "observation", "state_id": "s1"},
            {"ts": 1.2, "type": "policy_decision", "state_id": "s1", "policy": "rules",
             "candidate_id": "A1", "confidence": .95},
            {"ts": 1.3, "type": "candidate", "state_id": "s1", "candidate_id": "A1",
             "selected": {"id": "A1", "label": "PRESS @e1 Continue", "target_ref": "@e1"},
             "alternatives": [{"id": "A2", "label": "WAIT"}]},
            {"ts": 1.4, "type": "transaction", "candidate_id": "A1", "execution_path": "dom",
             "background": True, "dur_ms": 22.4, "status": "unverified"},
            {"ts": 1.5, "type": "state_diff", "before_state_id": "s1", "after_state_id": "s2",
             "changed": True, "text_changed": True},
            {"ts": 1.6, "type": "verification", "predicate": "text_contains", "passed": True},
            {"ts": 1.7, "type": "fastpath_end", "status": "verified"},
        ],
    }

    page = render_replay(bundle)
    assert "Transaction replay · before / after / why" in page
    assert "PRESS @e1 Continue" in page and "1 alternatives" in page
    assert "BEFORE" in page and "AFTER" in page and "execution dom" in page
    assert "target_element" in page and "Continue" in page
