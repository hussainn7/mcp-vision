from __future__ import annotations

import asyncio

from mcp_vision.partial_intent import PartialIntentWatcher, preparation_for
from mcp_vision.speech import InputLifecycle


def run(coro):
    return asyncio.run(coro)


def test_generic_capability_classification_has_no_task_branch():
    assert preparation_for("open the current website and fill the form").attach_browser
    assert preparation_for("explain the selected paragraph").capability == "observe"


def test_revised_partial_invalidates_generation_and_discards_artifacts():
    async def scenario():
        watcher = PartialIntentWatcher({"app_observed": lambda _text: "Mail"})
        assert await watcher.partial("open") is None
        first = await watcher.partial("open the")
        assert first and first.valid
        generation = first.generation
        assert await watcher.partial("explain this") is None
        assert not first.valid and watcher.generation > generation
    run(scenario())


def test_preparation_is_parallel_and_never_invokes_action():
    async def scenario():
        calls = []

        async def observe(name):
            await asyncio.sleep(.015)
            calls.append(name)
            return name

        lifecycle = InputLifecycle(
            observers={name: observe for name in ("app_observed", "window_captured", "provider_warm", "app_resolved")},
            action=lambda *_args: calls.append("action"),
        )
        await lifecycle.partial("open")
        start = asyncio.get_running_loop().time()
        prepared = await lifecycle.partial("open this")
        elapsed = asyncio.get_running_loop().time() - start
        assert prepared and elapsed < .05
        assert "action" not in calls
        await lifecycle.submit("open this page")
        assert calls[-1] == "action"
    run(scenario())


def test_artifact_adoption_and_discard():
    async def scenario():
        adopted = InputLifecycle(observers={"app_observed": lambda _: "Browser"})
        await adopted.partial("open")
        await adopted.partial("open this")
        await adopted.submit("open this page")
        assert adopted.state.prepared.values["app_observed"] == "Browser"

        discarded = InputLifecycle(observers={"app_observed": lambda _: "Browser"})
        await discarded.partial("open")
        await discarded.partial("open this")
        await discarded.submit("explain the selection")
        assert discarded.state.prepared is None
    run(scenario())


def test_cancel_releases_input_and_invalidates_preparation():
    released = []

    async def scenario():
        lifecycle = InputLifecycle(observers={"app_observed": lambda _: object()},
                                   release_input=lambda: released.append(True))
        await lifecycle.partial("open")
        prepared = await lifecycle.partial("open this")
        lifecycle.cancel()
        assert released == [True]
        assert not prepared.valid
        assert lifecycle.state.status == "cancelled"
    run(scenario())


def test_clarification_preserves_task_id_and_milestone_order():
    async def scenario():
        seen = []
        lifecycle = InputLifecycle(action=lambda state, text, _prepared: seen.append((state.task_id, text)))
        task_id = lifecycle.state.task_id
        lifecycle.ask("Which account?")
        await lifecycle.answer("the work account")
        assert lifecycle.state.task_id == task_id == seen[0][0]
        names = [event["name"] for event in lifecycle.metrics.events]
        assert names.index("clarification_requested") < names.index("clarification_answered")
        assert names.index("final_transcript") < names.index("first_useful_action")
    run(scenario())
