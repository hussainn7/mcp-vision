from mcp_vision.session_memory import compact_replay


def test_long_replay_keeps_recent_evidence_and_compresses_old_history():
    events = []
    for index in range(40):
        events.extend([
            {"type": "observation", "state_id": f"s{index}", "source": "dom", "elements": 40,
             "url": "https://fixture.test/" + "x" * 100},
            {"type": "policy_decision", "policy": "rules", "candidate_id": f"A{index}",
             "confidence": .95, "reason": "Unique semantic target matched " + "y" * 100},
            {"type": "transaction", "status": "unverified", "message": "clicked", "evidence": {"raw": "z" * 500}},
            {"type": "verification", "predicate": "text_contains", "expected": "Done", "passed": True},
        ])
    events.append({"type": "fastpath_end", "subgoal": "Finish settings", "status": "verified", "reason": "done"})
    memory = compact_replay({"events": events, "states": [{"text": "q" * 5000}]}, keep_recent=8)

    assert memory.recent_events == events[-8:]
    assert memory.important_observations and memory.resolved_decisions and memory.verified_outcomes
    assert memory.savings_ratio > .8
    assert memory.compact_chars < memory.original_chars


def test_memory_records_escalations_and_rejects_invalid_window():
    memory = compact_replay({"events": [
        {"type": "policy_decision", "candidate_id": None, "needs_system2": True,
         "reason": "Ambiguous target"},
        {"type": "candidate", "risk": "RESTRICTED_ACTION", "operation": "press"},
        {"type": "transaction", "status": "blocked", "message": "Needs confirmation"},
        {"type": "observation", "state_id": "s2"},
    ]}, keep_recent=1)
    assert any("System-2" in item for item in memory.constraints)
    assert any("Restricted" in item for item in memory.constraints)
    assert any("confirmation" in item for item in memory.known_failures)
