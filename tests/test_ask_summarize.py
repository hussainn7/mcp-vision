from mcp_vision.plan import plan_url, product_mention
from mcp_vision.summarize import summarize, _heuristic


def test_heuristic_pulls_prices_and_airlines():
    text = "Atlanta to San Francisco\nUnited 07:00 – 09:13 Non-stop US$617\nDelta US$937"
    out = _heuristic("flights to sf", text)
    assert "US$617" in out and "United" in out


def test_summarize_falls_back_without_model():
    out = summarize("flights", "Frontier US$390 nonstop ATL–SFO", backend="none", ok=True)
    assert "390" in out
    bad = summarize("flights", "nothing", backend="none", ok=False)
    assert "couldn't verify" in bad.lower()


def test_personal_product_opens_site_not_google():
    plan = plan_url("how many commits i did today on github", backend="none")
    assert "github.com" in plan["url"]
    assert "google.com/search" not in plan["url"]

    plan = plan_url("what's on my gmail", backend="none")
    assert "mail.google.com" in plan["url"]

    plan = plan_url("show my linkedin notifications", backend="none")
    assert "linkedin.com" in plan["url"]


def test_research_uses_public_search_without_google_bot_wall():
    plan = plan_url("what is a rust borrow checker", backend="none")
    assert "wikipedia.org/w/index.php" in plan["url"]
    assert "heat+pumps" in plan_url("Research heat pumps", backend="none")["url"]
    assert "google.com/search" in plan_url("search Google for rust borrow checker", backend="none")["url"]


def test_browser_command_is_not_copied_verbatim_into_search_query():
    plan = plan_url('Please open Chrome and search Google for cheap flights to Tokyo', backend='none')
    assert 'q=flights+to+Tokyo' in plan['url']
    assert 'Please+open+Chrome' not in plan['url']


def test_reuses_open_tab_for_product():
    tabs = [
        {"tab_id": "repo", "url": "https://github.com/hussainn7/mcp-vision", "title": "repo"},
        {"tab_id": "me", "url": "https://github.com/hussainn7", "title": "profile"},
    ]
    plan = plan_url("how many commits did i push today on github", backend="none", open_tabs=tabs)
    assert plan["source"] == "tab"
    assert plan["tab_id"] == "me"
    assert plan["url"].rstrip("/").endswith("hussainn7")


def test_personal_skips_deep_tab_opens_home():
    tabs = [{"tab_id": "repo", "url": "https://github.com/hussainn7/mcp-vision", "title": "repo"}]
    plan = plan_url("how many commits i did today on github", backend="none", open_tabs=tabs)
    assert plan["url"] == "https://github.com"
    assert plan["source"] == "rule"


def test_flights_and_ebay_still_route():
    assert "travel/flights" in plan_url("flights to SFO next week", backend="none")["url"]
    assert "ebay.com" in plan_url("ebay mechanical keyboard under 100", backend="none")["url"]


def test_product_mention():
    assert product_mention("stuff on GitHub please") == "github"


def test_setup_and_ask_cli_exist():
    from click.testing import CliRunner
    from mcp_vision.cli import cli
    runner = CliRunner()
    assert "setup" in runner.invoke(cli, ["--help"]).output
    assert "ask" in runner.invoke(cli, ["--help"]).output
    assert runner.invoke(cli, ["ask", "--help"]).exit_code == 0


def test_verified_flights_render_readable_offers_without_mixing_prices():
    evidence = ('URL: https://www.google.com/travel/flights\nANSWER:\n'
                '17:25\n – \n19:48\nFrontier\n5 hrs 23 min\nATL–SFO\nNon-stop\n'
                '194 kg CO2e\n-38% emissions\n0\n0\nUS$423\nround trip\n')
    result = summarize('find flights from ATL to SFO', evidence, backend=None)
    assert 'Frontier: 17:25–19:48, ATL–SFO, Non-stop, 5 hrs 23 min — US$423 round trip' in result
    assert 'not booked' in result and 'CO2e' not in result


def test_live_google_flight_card_format_with_meridiem_and_plain_dollars():
    evidence = ('URL: https://www.google.com/travel/flights\nANSWER:\n'
                '5:25\u202fPM\n – \n7:48\u202fPM\nFrontier\n5 hr 23 min\nATL–SFO\nNonstop\n'
                '194 kg CO2e\n-38% emissions\n0\n0\n$485\nround trip\n')
    result = summarize('find flights from ATL to SFO', evidence, backend=None)
    assert 'Frontier: 5:25 PM–7:48 PM' in result
    assert '$485 round trip' in result
