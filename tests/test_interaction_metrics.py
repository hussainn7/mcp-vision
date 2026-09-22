import json

from mcp_vision.interaction_metrics import InteractionTimeline


def test_interaction_timeline_is_bounded_to_safe_milestones(tmp_path):
    path = tmp_path / "metrics.jsonl"
    timeline = InteractionTimeline("voice", path=path)
    timeline.mark("first_transcript")
    timeline.mark("first_transcript")
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [event["milestone"] for event in events] == ["interaction_started", "first_transcript"]
    assert all(event["kind"] == "voice" and event["elapsed_ms"] >= 0 for event in events)
    assert all(set(event) == {"ts", "interaction_id", "kind", "milestone", "elapsed_ms"} for event in events)
