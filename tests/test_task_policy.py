import pytest
from mcp_vision.context import Context, ContextElement
from mcp_vision.contextual import infer_capability, package_context
from mcp_vision.task_policy import TaskConstraints


@pytest.mark.parametrize('prompt,mode', [
    ('Which option should I choose?', 'ask'), ('Why is submit disabled?', 'ask'),
    ('Where is the export button?', 'guide'), ('Export this as CSV.', 'act'),
    ('Fill this but don’t submit.', 'act'), ('Can I delete this?', 'ask'),
    ('Just show me how to send this.', 'guide'), ('Maybe change this?', 'ask'),
])
def test_intent(prompt, mode):
    assert infer_capability(prompt) == mode


def test_context_prioritizes_clicked_target_and_excludes_screenshot():
    context = Context(source='chrome', clicked_element=ContextElement(name='Error'),
                      focused_element=ContextElement(name='Search'), screenshot_reference='secret.png',
                      dom_context={'text': 'Connection refused'})
    package = package_context(context)
    assert package['target']['name'] == 'Error'
    assert 'screenshot_reference' not in package
    assert package['nearby']['text'] == 'Connection refused'


@pytest.mark.parametrize('mode', ['ask', 'guide'])
def test_read_only(mode):
    with pytest.raises(PermissionError):
        TaskConstraints().check(mode, 'fill', {'name': 'Email'}, value='x')


def test_prohibitions_are_executable():
    c = TaskConstraints.parse("Fill this. Only use factual information. Don't submit. Don't send anything. Don't delete anything. Do not leave this page.")
    assert c.no_submit and c.no_send and c.no_delete and c.stay_on_page and c.factual
    with pytest.raises(PermissionError):
        c.check('act', 'click', {'name': 'Next'})
    with pytest.raises(PermissionError):
        c.check('act', 'fill', {'name': 'Experience'}, value='10 years', source='2 years')
    c.check('act', 'fill', {'name': 'Experience'}, value='2 years', source='Experience: 2 years')


def test_only_this_field_is_resolved():
    c = TaskConstraints.parse('Only change this field.', Context(clicked_element=ContextElement(name='Email')))
    assert c.only_field == 'Email'
    with pytest.raises(PermissionError):
        c.check('act', 'fill', {'name': 'Name'}, value='Jane')


def test_package_total_budget_includes_adversarial_attributes():
    import json
    context = Context(source='chrome', clicked_element=ContextElement(name='Click', attributes={str(i):'x'*10000 for i in range(500)}),
                      dom_context={'controls':[{'name':'x'*10000} for i in range(80)]})
    assert len(json.dumps(package_context(context))) < 14000
