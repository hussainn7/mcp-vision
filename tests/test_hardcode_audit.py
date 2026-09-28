from pathlib import Path


ROOT = Path(__file__).parents[1]
RUNTIME = [
    ROOT / "src/mcp_vision/plan.py",
    ROOT / "src/mcp_vision/controller.py",
    ROOT / "src/mcp_vision/request_routing.py",
    ROOT / "src/mcp_vision/partial_intent.py",
    ROOT / "src/mcp_vision/summarize.py",
    ROOT / "src/mcp_vision/tasks.py",
    ROOT / "src/mcp_vision/server.py",
    ROOT / "src/mcp_vision/reasoning/intent.py",
    ROOT / "src/mcp_vision/reasoning/reasoners.py",
]


def test_runtime_has_no_task_named_planner_branches():
    forbidden = ("flight", "airport", "calculator", "new note", "google.com/travel")
    for path in RUNTIME:
        text = path.read_text().lower()
        assert not any(term in text for term in forbidden), path


def test_operation_vocabulary_is_general():
    from reasoning.intent import Operation
    assert {item.value for item in Operation} == {
        "OPEN_URL", "ENTER_TEXT", "SELECT", "PRESS", "WAIT_FOR", "VERIFY", "ASK_USER", "REPLAN"
    }
