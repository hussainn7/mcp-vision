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


def test_ask_url_routing():
    from mcp_vision.ask import _search_url
    assert "travel/flights" in _search_url("flights to SFO next week")
    assert "ebay.com" in _search_url("ebay mechanical keyboard")
    assert "mail.google" in _search_url("check my gmail inbox")
    assert "google.com/search" in _search_url("what is rust borrow checker")


def test_setup_and_ask_cli_exist():
    from click.testing import CliRunner
    from mcp_vision.cli import cli
    runner = CliRunner()
    assert "setup" in runner.invoke(cli, ["--help"]).output
    assert "ask" in runner.invoke(cli, ["--help"]).output
    # ask with heuristic only against isolated may need network; just ensure option parses
    assert runner.invoke(cli, ["ask", "--help"]).exit_code == 0
