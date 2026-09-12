import asyncio

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from mcp_vision.cli import cli
from mcp_vision.missions import Mission, RECIPES, brief
from mcp_vision.server import _mcp


def test_brief_preserves_intent_without_claiming_execution():
    result = brief(Mission(goal="  Compare these plans  ", success="A sourced table"))
    assert result["mission"]["goal"] == "Compare these plans"
    assert result["executed"] is False and result["needs_destination"]
    assert "Observe only" in result["boundary"]
    assert "executed is null" in result["prompt"]
    assert "A sourced table" in result["prompt"]


@pytest.mark.parametrize("data", [{"goal": "   "}, {"goal": "x" * 4001},
    {"goal": "Inspect", "url": "file:///etc/passwd"},
    {"goal": "Inspect", "url": "https://user:secret@example.com"},
    {"goal": "Inspect", "mode": "autonomous"}])
def test_invalid_missions_are_rejected(data):
    with pytest.raises(ValidationError):
        Mission(**data)


def test_recipes_make_valid_portable_briefs():
    for recipe in RECIPES:
        result = brief(Mission(**{k: recipe[k] for k in ("goal", "success", "mode")}))
        assert recipe["goal"] in result["prompt"]
        if recipe["mode"] == "draft":
            assert "Never send" in result["boundary"]


def test_task_cli_and_validation():
    runner = CliRunner()
    assert runner.invoke(cli, ["task", "Check this site", "--url", "https://example.com"]).exit_code == 0
    assert runner.invoke(cli, ["task", "Check this site", "--url", "file:///tmp"]).exit_code != 0


def test_mcp_exposes_mission_prompt_and_grounded_action():
    async def check():
        from fastmcp import Client
        async with Client(_mcp()) as client:
            assert "browser_act" in {t.name for t in await client.list_tools()}
            assert "mission" in {p.name for p in await client.list_prompts()}
            result = await client.get_prompt("mission", {"goal": "Inspect the site"})
            assert "Inspect the site" in str(result)
    asyncio.run(check())
