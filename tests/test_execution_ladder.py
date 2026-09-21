from mcp_vision.execution_ladder import AttemptOutcome, ExecutionMethod, ExecutionTrace


def test_ladder_falls_back_only_after_checked_no_effect():
    trace = ExecutionTrace()
    assert trace.can_fallback
    trace.add(ExecutionMethod.AX_BACKGROUND, AttemptOutcome.DIDNT, background=True,
              detail="unsupported")
    assert trace.can_fallback
    trace.add(ExecutionMethod.PID_KEYBOARD, AttemptOutcome.UNKNOWN, background=True,
              detail="events dispatched")
    assert not trace.can_fallback
    assert trace.evidence()["execution_path"] == "pid_keyboard"
    assert trace.evidence()["background"] is True


def test_worked_foreground_path_is_explicit():
    trace = ExecutionTrace()
    trace.add(ExecutionMethod.AX_BACKGROUND, AttemptOutcome.DIDNT, background=True)
    trace.add(ExecutionMethod.AX_FOREGROUND, AttemptOutcome.WORKED, background=False)
    assert trace.chosen is ExecutionMethod.AX_FOREGROUND
    assert trace.background is False
