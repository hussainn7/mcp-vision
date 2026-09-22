from __future__ import annotations

from mcp_vision.context import Context, ContextBounds, ContextElement, Point
from mcp_vision.contextual import _dedupe_answer, answer_context, infer_capability, package_context
from mcp_vision.execution import ExecutionBackend, create_execution_backend


def test_shared_context_is_bounded_and_compact():
    context = Context(
        source="chrome",
        source_application="Google Chrome",
        url="https://example.test/path",
        selected_text="x" * 13000,
        clicked_element=ContextElement(role="button", name="Save",
                                       bounds=ContextBounds(x=1, y=2, width=3, height=4)),
        dom_context={"text": "nearby", "empty": ""},
    )
    assert len(context.selected_text) == 12000
    compact = context.compact()
    assert compact["source"] == "chrome"
    assert "created_at" not in compact and "context_id" not in compact
    assert "empty" not in compact["dom_context"]


def test_capability_is_inferred_without_forcing_a_choice():
    assert infer_capability("What does this error mean?") == "ask"
    assert infer_capability("Where do I change this setting?") == "guide"
    assert infer_capability("Which button do I press next?") == "guide"
    assert infer_capability("Fill this application but don't submit") == "act"
    assert infer_capability("Show me how to export") == "guide"
    assert infer_capability("Turn this off") == "act"
    assert infer_capability("Complete this application") == "act"
    assert infer_capability("Full out this form") == "act"
    assert infer_capability("Are you able to create a new note?") == "act"
    assert infer_capability("Would you be able to make a new tab?") == "act"


def test_packaged_context_marks_the_exact_cursor_target():
    context = Context(
        source='macos', source_application='Safari', cursor_position=Point(x=320, y=180),
        focused_element=ContextElement(role='AXTextField', name='Email address'),
    )
    packaged = package_context(context)
    assert packaged['target']['name'] == 'Email address'
    assert packaged['pointer'] == {'x': 320.0, 'y': 180.0}


def test_context_only_answer_always_returns_in_popup_shape():
    context = Context(source="chrome", selected_text="Connection refused", user_request="What does this mean?")
    result = answer_context(context, provider="definitely-unavailable")
    assert result["capability"] == "ask"
    assert "Connection refused" in result["answer"]
    assert result["provider"] == "context-only"


def test_repeated_provider_answer_is_shown_once():
    block = ('This is a complete answer with enough detail to be useful.\n\n'
             'It should appear exactly once in the popup.')
    assert _dedupe_answer(block + '\n\n' + block) == block


def test_general_answer_prompt_does_not_treat_screen_context_as_a_requirement(monkeypatch):
    seen = []
    def chat(messages, tools=None):
        seen.extend(messages)
        return {'content': 'A direct general answer.'}
    monkeypatch.setattr('backends.get_chat', lambda _backend: chat)
    result = answer_context(Context(source_application='ChatGPT', user_request='Explain recursion'),
                            provider='local')
    assert result['answer'] == 'A direct general answer.'
    assert 'Never refuse merely because the answer is absent from CONTEXT' in seen[0]['content']


def test_execution_boundary_rejects_unknown_mode():
    try:
        create_execution_backend(browser_mode="other")
    except ValueError as exc:
        assert "live or isolated" in str(exc)
    else:
        raise AssertionError("unknown execution mode was accepted")


def test_execution_protocol_describes_current_runtime_surface():
    expected = {"tabs", "use_tab", "open_tab", "navigate", "snapshot", "click", "fill",
                "select", "set_checked", "upload", "scroll", "verify_text", "screenshot", "close"}
    assert expected <= set(ExecutionBackend.__dict__)
