import asyncio
from mcp_vision.browser import BrowserSnapshot, Receipt
from mcp_vision.controller import compile_mission, run_controller, MAX_STEPS
from mcp_vision.plan import plan_url
from mcp_vision.summarize import summarize

QUERY = 'how many commits did I do today on github'


def snap(text, url='https://github.com', links=None, facts=None, identity=None):
    return BrowserSnapshot(snapshot_id='fresh', title='Page', url=url, text=text, elements=links or [],
                           facts=facts or [], identity=identity or {})


class Runtime:
    def __init__(self, snapshots, status='verified', executed=True):
        self.snapshots = iter(snapshots)
        self.last = None
        self.actions = []
        self.status = status
        self.executed = executed

    async def tabs(self):
        return {'connected': True, 'tabs': []}

    async def snapshot(self):
        self.last = next(self.snapshots, self.last)
        return self.last

    async def navigate(self, url):
        self.actions.append(('navigate', url))
        return Receipt(status=self.status, action='navigate', message='receipt', executed=self.executed)

    async def scroll(self, snapshot_id, delta_y):
        self.actions.append(('scroll', snapshot_id))
        return Receipt(status=self.status, action='scroll', message='receipt', executed=self.executed)


def run(runtime, query=QUERY, **kwargs):
    mission = compile_mission(query, plan_url(query, backend=None))
    return asyncio.run(run_controller(runtime, mission, **kwargs))


def test_mission_requires_count_today_evidence():
    mission = compile_mission(QUERY, {'url': 'https://github.com'})
    assert all(word in mission.success for word in ('count', 'today', 'source URL'))
    assert mission.mode == 'observe'


def test_advances_then_stops_on_grounded_count():
    runtime = Runtime([snap('Your account', links=[{'role':'link', 'name':'Your profile', 'href':'https://github.com/person'}]),
                       snap('Signed in as person\n7 commits today', 'https://github.com/person')])
    result = run(runtime)
    assert result['ok'] and result['steps'] == 2
    assert runtime.actions == [('navigate', 'https://github.com/person')]
    assert '7 commits today' in result['summary'] and 'https://github.com/person' in result['summary']
    assert result['trace'][0]['postcondition_verified']


def test_cap_even_when_every_action_verified():
    runtime = Runtime([snap(f'No answer, page {i}') for i in range(MAX_STEPS)])
    result = run(runtime)
    assert not result['ok'] and result['steps'] == MAX_STEPS
    assert len(runtime.actions) == MAX_STEPS - 1


def test_receipt_never_means_success_and_retries_bounded():
    runtime = Runtime([snap('Your account')])
    result = run(runtime)
    assert not result['ok'] and len(runtime.actions) == 3


def test_unknown_execution_reobserved_before_next_action():
    runtime = Runtime([snap('Your account'), snap('Signed in as person\n2 commits today')], executed=None)
    result = run(runtime)
    assert result['ok'] and len(runtime.actions) == 1


def test_auth_blocker_stops_before_actions():
    for url, text in [('https://github.com/login', 'Sign in to GitHub'),
                      ('https://challenges.cloudflare.com', 'Verify you are human')]:
        runtime = Runtime([snap(text, url)])
        result = run(runtime)
        assert not result['ok'] and not runtime.actions and 'required' in result['blocker']


def test_denied_action_stops():
    runtime = Runtime([snap('Your account')], status='blocked')
    assert not run(runtime)['ok']
    assert len(runtime.actions) == 1


def test_incomplete_cannot_surface_unverified_count():
    assert '99' not in summarize(QUERY, '99 commits today', ok=False, backend=None)


def test_contributions_not_commits_and_account_required():
    for text in ('Signed in as person\n5 contributions today', '5 commits today',
                 'Signed in as person\n5 commits yesterday'):
        assert not run(Runtime([snap(text)]))['ok']


def test_research_definition():
    result = run(Runtime([snap('Kubernetes is an open source system for managing containerized applications.',
                               'https://www.google.com/search?q=kubernetes')]), query='what is kubernetes')
    assert result['ok'] and 'containerized' in result['summary']


def test_dangerous_links_are_not_followed():
    runtime = Runtime([snap('Your account', links=[{'role':'link', 'name':'Send inbox', 'href':'https://github.com/send'}])])
    run(runtime)
    assert all(action[0] == 'scroll' for action in runtime.actions)


def test_named_product_ignores_bad_model_routing(monkeypatch):
    monkeypatch.setattr('mcp_vision.plan.plan_with_model', lambda *a: {'url':'https://www.google.com/search?q=oops'})
    assert plan_url(QUERY)['url'] == 'https://github.com'
    assert plan_url('what emails do i have unread in gmail')['url'] == 'https://mail.google.com'


def test_ask_wires_controller_and_keeps_write_policy(monkeypatch):
    from mcp_vision.ask import run_ask
    from mcp_vision.core.models import Policy
    runtime = Runtime([snap('Your account'), snap('Signed in as person\n3 commits today')])
    runtime.closed = False

    async def open_tab(url):
        return Receipt(status='verified', action='open_tab', executed=True, message='opened')

    async def close():
        runtime.closed = True

    runtime.open_tab = open_tab
    runtime.close = close

    def factory(**kwargs):
        assert kwargs['allow_writes'] is False
        assert kwargs['pause_for_challenges'] is False
        assert not kwargs['governor'].allow(Policy.RESTRICTED_ACTION, 'Send or checkout')
        return runtime

    monkeypatch.setattr('mcp_vision.native_browser.NativeBrowserRuntime', factory)
    result = asyncio.run(run_ask(QUERY, backend=None))
    assert result['ok'] and result['mission']['mode'] == 'observe'
    assert runtime.closed and len(runtime.actions) == 1


def test_ask_recovers_from_stale_reused_tab(monkeypatch):
    from mcp_vision.ask import run_ask
    page = snap('Inbox', 'https://mail.google.com/mail/u/0/#inbox',
                facts=[{'kind':'unread_email', 'sender':'Teacher', 'subject':'Quiz'}],
                identity={'value':'user@example.com', 'via':'google account control'})
    runtime = Runtime([page])
    runtime.opened = 0
    runtime.closed = False

    async def tabs():
        return {'connected': True, 'tabs': [{'tab_id':'old', 'url':page.url, 'title':'Inbox'}]}

    async def use_tab(_tab_id, _expected_url):
        return Receipt(status='stale', action='use_tab', message='moved', executed=False)

    async def open_tab(_url):
        runtime.opened += 1
        return Receipt(status='verified', action='open_tab', message='opened', executed=True)

    async def close():
        runtime.closed = True

    runtime.tabs, runtime.use_tab, runtime.open_tab, runtime.close = tabs, use_tab, open_tab, close
    monkeypatch.setattr('mcp_vision.native_browser.NativeBrowserRuntime', lambda **_kwargs: runtime)
    result = asyncio.run(run_ask('what unread emails are in my gmail inbox?', backend=None))
    assert result['ok'] and runtime.opened == 1 and runtime.closed


def test_email_and_course_evidence_use_same_loop():
    email = run(Runtime([snap('Your account'), snap('Signed in as person\nUnread From: Teacher Subject: Quiz', 'https://mail.google.com')]),
                query='unread email in gmail')
    due = run(Runtime([snap('Your account'), snap('Signed in as person\nAll courses\nCourse: Math Due: Today Assignment: Quiz', 'https://icollege.gsu.edu')]),
              query='what is due in my icollege')
    assert email['ok'] and due['ok']
    assert 'Quiz' in email['summary'] and 'Math' in due['summary']


def test_gmail_structured_unread_facts_are_evidence():
    page = snap('Inbox', 'https://mail.google.com/mail/u/0/#inbox',
                facts=[{'kind':'unread_email', 'sender':'Teacher', 'subject':'Quiz', 'date':'Today'}],
                identity={'value':'user@example.com', 'via':'google account control'})
    result = run(Runtime([page]), query='what unread emails are in my gmail inbox?')
    assert result['ok']
    assert 'Teacher' in result['evidence'] and 'Quiz' in result['evidence']
    assert 'user@example.com' in result['evidence']


def test_navigation_retry_has_fresh_observation():
    runtime = Runtime([snap('Your account', links=[{'role':'link', 'name':'Your profile', 'href':'https://github.com/person'}])])
    result = run(runtime)
    assert not result['ok']
    assert runtime.actions == [('navigate', 'https://github.com/person')] * 3
    assert all(t['postcondition_verified'] is False for t in result['trace'])


def test_cap_is_hard():
    import pytest
    with pytest.raises(ValueError):
        run(Runtime([]), max_steps=MAX_STEPS + 1)


def test_public_research_follows_observed_external_source():
    from mcp_vision.controller import next_link
    mission = compile_mission('research heat pumps', {'url': 'https://www.google.com/search?q=heat+pumps'})
    page = snap('Results', mission.url, links=[
        {'role': 'link', 'name': 'Heat pumps explained', 'href': 'https://energy.gov/heat-pumps'}])
    assert next_link(mission, page, {mission.url}) == 'https://energy.gov/heat-pumps'
    private = compile_mission('my gmail inbox', {'url': 'https://mail.google.com'})
    page.elements[0]['name'] = 'Inbox messages'
    assert next_link(private, page, set()) is None


def test_flight_mission_requires_actual_options():
    mission = compile_mission('flights from ATL to SFO tomorrow', {'url': 'https://www.google.com/travel/flights'})
    assert all(word in mission.success for word in ('origin', 'destination', 'dates', 'price'))


def test_provider_failure_surfaces_without_pointless_scrolls(monkeypatch):
    from backends import BackendError
    def fail(*a):
        raise BackendError('Local model unavailable')
    monkeypatch.setattr('mcp_vision.controller.model_evidence', fail)
    runtime = Runtime([snap('Search results')])
    result = run(runtime, query='research heat pumps', backend='local')
    assert not result['ok'] and 'Local model unavailable' in result['blocker']
    assert not runtime.actions


def test_public_research_never_uses_model_invented_url(monkeypatch):
    monkeypatch.setattr('mcp_vision.plan.plan_with_model', lambda *a: {'url': 'https://example.com/not-real'})
    assert 'wikipedia.org/w/index.php?search=' in plan_url('Look up heat pumps')['url']
    assert '/search?q=' in plan_url('Search Google for heat pumps')['url']


def test_fenced_evidence_is_supported_without_accepting_fabricated_quotes(monkeypatch):
    import json
    from mcp_vision.controller import model_evidence
    mission = compile_mission('research heat pumps', {'url': 'https://www.google.com/search?q=heat+pumps'})
    page = snap('Heat pumps move thermal energy between spaces.')
    quote = page.text
    def chat(*a, **k):
        return {'content': '```json\n' + json.dumps({'complete': True, 'quotes': [quote]}) + '\n```'}
    monkeypatch.setattr('backends.get_chat', lambda _: chat)
    assert model_evidence(mission, page, 'local') == page.text
    quote = 'Heat pumps produce unlimited free energy.'
    assert model_evidence(mission, page, 'local') == ''


def test_page_quotes_allow_layout_whitespace_but_not_new_facts(monkeypatch):
    import json
    from mcp_vision.controller import model_evidence
    mission = compile_mission('flights from ATL to SFO September 28', {'url': 'https://www.google.com/travel/flights'})
    page = snap('Frontier\nATL–SFO\nUS$423\nround\u00a0trip')
    def chat(*a, **k):
        return {'content': json.dumps({'complete': True, 'quotes': ['Frontier ATL–SFO US$423 round trip']})}
    monkeypatch.setattr('backends.get_chat', lambda _: chat)
    assert 'US$423' in model_evidence(mission, page, 'local')


def test_flight_options_require_observed_route_and_ordered_dates():
    from mcp_vision.controller import flight_evidence
    query = 'Find round-trip flights from ATL to SFO September 28 to October 2, 2026'
    text = ('Track prices departing 2026-09-28 and returning 2026-10-02\n'
            '17:25\n–\n19:48\nFrontier\n5 hrs 23 min\nATL–SFO\nNon-stop\nUS$423\nround trip')
    assert 'Frontier: 17:25–19:48' in flight_evidence(query, snap(text))
    assert not flight_evidence(query, snap(text.replace('2026-10-02', '2026-10-03')))
    assert not flight_evidence(query, snap(text.replace('ATL–SFO', 'SFO–ATL')))
    assert not flight_evidence(query, snap(text.replace('departing', 'returning')))


def test_relative_date_city_flight_evidence_is_deterministic(monkeypatch):
    import mcp_vision.plan
    from datetime import date
    from mcp_vision.controller import flight_evidence
    monkeypatch.setattr(mcp_vision.plan, '_relative_flight_dates',
                        lambda _q: (date(2026, 9, 21), date(2026, 9, 27), 'next week'))
    text = ('Departing Mon, Sep 21 Returning Sun, Sep 27\n'
            '17:25\n–\n19:48\nFrontier\n5 hrs 23 min\nATL–SFO\nNon-stop\nUS$423\nround trip')
    result = flight_evidence('Find flights from Atlanta to SF next week', snap(text))
    assert 'ATL–SFO' in result and 'US$423' in result


def test_public_research_can_use_exact_article_excerpts_without_a_model():
    page = snap('Heat pumps transfer heat rather than generating it, which can reduce electricity use.\n'
                'Modern heat pumps can provide both heating and cooling in many climates.',
                'https://energy.gov/heat-pumps')
    result = run(Runtime([
        snap('Results', 'https://www.google.com/search?q=heat+pumps', links=[
            {'role': 'link', 'name': 'Heat pumps explained', 'href': page.url}]),
        page,
    ]),
        query='research heat pumps', backend=None)
    assert result['ok'] and 'transfer heat' in result['summary']
