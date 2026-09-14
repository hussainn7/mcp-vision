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
    assert "did not succeed" in bad.lower()


def test_personal_product_opens_site_not_google():
    plan = plan_url("how many commits i did today on github", backend="none")
    assert "github.com" in plan["url"]
    assert "google.com/search" not in plan["url"]

    plan = plan_url("what's on my gmail", backend="none")
    assert "mail.google.com" in plan["url"]

    plan = plan_url("show my linkedin notifications", backend="none")
    assert "linkedin.com" in plan["url"]


def test_research_still_uses_google():
    plan = plan_url("what is a rust borrow checker", backend="none")
    assert "google.com/search" in plan["url"]


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
