from types import SimpleNamespace

import pytest

from mcp_vision.task_state import TaskState, TaskStatus, VerifiedEffect


def observed(state_id="s1", epoch=1, root_id="root"):
    return SimpleNamespace(state_id=state_id, root_id=root_id, epoch=epoch, content_hash=f"hash-{state_id}")


def test_task_state_serializes_and_resumes_same_task_with_planner_slots():
    task = TaskState.create("Complete the observed workflow", missing_slots=("planner_slot_7",))
    task = task.start().record_observation(observed())
    task = task.checkpoint_question("Which value should I use?", missing_slots=("planner_slot_7",),
                                    provider_continuation={"cursor": "opaque-provider-value"})

    restored = TaskState.model_validate_json(task.model_dump_json())
    resumed = restored.merge_reply("supplied value")

    assert resumed.task_id == task.task_id
    assert resumed.status is TaskStatus.RUNNING
    assert resumed.supplied_slots == {"planner_slot_7": "supplied value"}
    assert resumed.missing_slots == () and resumed.pending_question is None
    assert resumed.provider_continuation == {
        "cursor": "opaque-provider-value", "latest_reply": "supplied value"}
    assert resumed.metrics.questions == resumed.metrics.resumes == 1


def test_task_state_rejects_stale_or_changed_root_on_resume():
    task = TaskState.create("Continue").start().record_observation(observed(epoch=4))
    with pytest.raises(ValueError, match="stale"):
        task.validate_resume_epoch(observed("s2", epoch=4))
    with pytest.raises(ValueError, match="root changed"):
        task.validate_resume_epoch(observed("s3", epoch=5, root_id="other"))
    task.validate_resume_epoch(observed("s4", epoch=5))


def test_cancelled_task_cannot_merge_a_reply():
    task = TaskState.create("Continue").checkpoint_question("Continue?").cancel()
    restored = TaskState.model_validate(task.model_dump(mode="json"))
    assert restored.status is TaskStatus.CANCELLED
    assert restored.metrics.cancellations == 1
    with pytest.raises(ValueError, match="Cancelled"):
        restored.merge_reply("yes")


def test_verified_effect_advances_cursor_once():
    task = TaskState.create("Mutate once").start()
    effect = VerifiedEffect(effect_id="effect-1", operation="type", target="@e0", value="unique")
    task = task.record_effect(effect).record_effect(effect)
    assert task.plan_cursor == 1
    assert task.metrics.verified_effects == 1
    assert task.completed_verified_effects == (effect,)
