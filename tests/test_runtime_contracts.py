import asyncio

from mcp_vision.browser import BrowserRuntime, Receipt, origin
from mcp_vision.redaction import redact
from mcp_vision.core.governor import Governor, classify
from mcp_vision.core.models import Policy
from runtime import did_state_change


def test_receipts_do_not_infer_task_completion():
    for status in ("verified", "unverified", "blocked", "stale", "error"):
        assert Receipt(status=status, action="test", message="test").task_complete is False
    assert Receipt(status="error", action="click", message="timeout", executed=None).executed is None


def test_missing_fingerprint_is_unknown():
    fingerprint = {"url": "https://example.com", "title": "Example", "n": 1,
                   "scroll": 0, "text": "hello"}
    assert did_state_change(None, fingerprint) is None
    assert did_state_change(fingerprint, None) is None


def test_origin_normalization():
    assert origin("https://EXAMPLE.com:443/path") == "https://example.com"
    assert origin("http://[::1]:8000/path") == "http://[::1]:8000"


def test_scope_fails_before_launch():
    runtime = BrowserRuntime(allowed_origins=["https://example.com"])
    receipt = asyncio.run(runtime.navigate("https://elsewhere.test"))
    assert receipt.executed is False and receipt.status == "error"
    assert runtime.page is None


def test_redact_credentials_in_nested_errors():
    value = redact({"api_key": "secret", "error": "https://host.test/?key=abc&x=2", "headers": {"Authorization": "Bearer secret"}})
    assert "secret" not in str(value) and "key=abc" not in str(value)
    assert "x=2" in str(value)


def test_enter_cannot_submit_without_confirmation():
    policy = classify("press_key_combination", keys=["Enter"])
    assert policy == Policy.RESTRICTED_ACTION
    assert not Governor().allow(policy, "submit")


def test_plain_execution_judge_does_not_prove_task_completion():
    from judge import heuristic_judge
    verdict = heuristic_judge([
        {"type": "tool_call", "tool": "read", "args": {}, "result": "ok"},
        {"type": "run_end", "status": "ok", "answer": "done"},
    ])
    assert verdict["scope"] == "execution_quality"
    assert verdict["task_completion"] == "unknown"


def test_unverified_trace_cannot_become_a_learned_skill():
    from skills import distill
    events = [{"type": "run_start", "task": "save a draft"},
              {"type": "tool_call", "tool": "click", "result": "clicked"},
              {"type": "run_end", "status": "ok", "answer": "done"}]
    assert distill(events) is None
    events.append({"type": "completion", "status": "verified"})
    assert distill(events) is not None
