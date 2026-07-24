"""
Persistent plan management with AX-tree verification.

Maintains an ordered list of subtasks. After each action, verifies success
with a cheap AX-tree diff (did the expected element appear/change state?)
instead of a full re-perception + vision call.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger


class StepStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    VERIFICATION_FAILED = "verification_failed"


@dataclass
class PlanStep:
    """A single step in the execution plan."""
    description: str
    expected_element: str | None = None       # element label we expect to appear/change
    expected_role: str | None = None          # expected role (button, textbox, etc.)
    expected_state: dict[str, Any] | None = None  # e.g., {"enabled": True, "focused": True}
    action: str | None = None                 # the tool call that should achieve this
    status: StepStatus = StepStatus.PENDING
    retry_count: int = 0
    max_retries: int = 2
    result: str | None = None
    verified: bool = False


@dataclass
class Plan:
    """Persistent execution plan with verification."""
    task: str
    steps: list[PlanStep] = field(default_factory=list)
    current_step_index: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def current_step(self) -> PlanStep | None:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    @property
    def is_complete(self) -> bool:
        return self.current_step_index >= len(self.steps)

    @property
    def completed_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == StepStatus.COMPLETED]

    def advance(self) -> bool:
        """Move to next step. Returns True if there's a next step."""
        self.current_step_index += 1
        self.updated_at = time.time()
        return not self.is_complete

    def mark_current_failed(self, result: str):
        """Mark current step as failed with result."""
        step = self.current_step
        if step:
            step.status = StepStatus.FAILED
            step.result = result
            step.retry_count += 1
            self.updated_at = time.time()

    def mark_current_verification_failed(self, result: str):
        """Mark current step as verification failed."""
        step = self.current_step
        if step:
            step.status = StepStatus.VERIFICATION_FAILED
            step.result = result
            step.retry_count += 1
            self.updated_at = time.time()

    def mark_current_completed(self, result: str):
        """Mark current step as completed."""
        step = self.current_step
        if step:
            step.status = StepStatus.COMPLETED
            step.result = result
            step.verified = True
            self.updated_at = time.time()


class PlanManager:
    """
    Manages persistent plans with AX-tree verification.

    Key idea: instead of full re-perception on every cycle, maintain a plan
    and verify each step with a cheap AX-tree diff. Only fall back to expensive
    perception when verification fails.
    """

    def __init__(self, ax_verifier_func=None):
        """
        Args:
            ax_verifier_func: callable that returns current AX elements.
                             Should be `get_accessibility_elements` from accessibility.py
        """
        self.current_plan: Plan | None = None
        self.ax_verifier = ax_verifier_func
        self._last_ax_snapshot: list[dict[str, Any]] = []

    def create_plan(self, task: str, steps: list[dict[str, Any]]) -> Plan:
        """Create a new plan from a list of step descriptions."""
        plan_steps = []
        for step_data in steps:
            if isinstance(step_data, str):
                plan_steps.append(PlanStep(description=step_data))
            elif isinstance(step_data, dict):
                plan_steps.append(PlanStep(
                    description=step_data.get("description", ""),
                    expected_element=step_data.get("expected_element"),
                    expected_role=step_data.get("expected_role"),
                    expected_state=step_data.get("expected_state"),
                    action=step_data.get("action"),
                    max_retries=step_data.get("max_retries", 2),
                ))

        self.current_plan = Plan(task=task, steps=plan_steps)
        self._last_ax_snapshot = []
        logger.info(f"Created plan with {len(plan_steps)} steps for: {task}")
        return self.current_plan

    def get_current_action(self) -> str | None:
        """Get the action for the current step."""
        step = self.current_plan.current_step if self.current_plan else None
        return step.action if step else None

    def get_current_step_description(self) -> str | None:
        """Get the description of the current step."""
        step = self.current_plan.current_step if self.current_plan else None
        return step.description if step else None

    def execute_and_verify(self, action: str, execute_func) -> tuple[str, bool, bool]:
        """
        Execute an action and verify with AX-tree diff.

        Returns:
            (result, success, verified)
            - result: tool execution result string
            - success: whether tool execution succeeded
            - verified: whether AX-tree verification passed
        """
        if not self.current_plan:
            return "No active plan", False, False

        step = self.current_plan.current_step
        if not step:
            return "Plan complete", True, True

        # Execute the action
        step.status = StepStatus.IN_PROGRESS
        self.current_plan.updated_at = time.time()

        result = execute_func(action)
        success = not result.startswith("ERROR")

        if not success:
            self.current_plan.mark_current_failed(result)
            logger.warning(f"Action failed: {action} -> {result}")
            return result, False, False

        # Verify with AX-tree diff if we have expectations
        verified = self._verify_step(step)

        if verified:
            self.current_plan.mark_current_completed(result)
            logger.info(f"Step verified: {step.description}")
        else:
            self.current_plan.mark_current_verification_failed(result)
            logger.warning(f"Step verification failed: {step.description}")

        return result, success, verified

    def _verify_step(self, step: PlanStep) -> bool:
        """Verify step completion using cheap AX-tree diff."""
        if not self.ax_verifier:
            # No verifier available - assume success
            return True

        # If no specific expectation, we can't verify - assume success
        if not step.expected_element and not step.expected_role and not step.expected_state:
            return True

        try:
            current_elements = self.ax_verifier(use_cache=False)
            if not current_elements:
                logger.debug("No AX elements for verification")
                return True  # Can't verify, don't block

            # Check if expected element/state appeared
            if step.expected_element:
                for elem in current_elements:
                    label = elem.get("label", "").lower()
                    if step.expected_element.lower() in label:
                        # Check role match if specified
                        if step.expected_role:
                            if elem.get("role", "").lower() == step.expected_role.lower():
                                return True
                        else:
                            return True

            # Check expected state
            if step.expected_state:
                for elem in current_elements:
                    if step.expected_element and step.expected_element.lower() not in elem.get("label", "").lower():
                        continue
                    match = True
                    for key, expected_val in step.expected_state.items():
                        actual_val = elem.get(key)
                        if actual_val != expected_val:
                            match = False
                            break
                    if match:
                        return True

            logger.debug(f"Verification failed for step: {step.description}")
            return False

        except Exception as e:
            logger.debug(f"AX verification error: {e}")
            return True  # Don't block on verification errors

    def should_retry_current(self) -> bool:
        """Check if current step should be retried."""
        step = self.current_plan.current_step if self.current_plan else None
        if not step:
            return False
        return step.retry_count < step.max_retries

    def advance_or_retry(self) -> tuple[bool, bool]:
        """
        Advance to next step or retry current.

        Returns:
            (should_continue, is_retry)
        """
        if not self.current_plan:
            return False, False

        step = self.current_plan.current_step
        if not step:
            return False, False

        if step.status in (StepStatus.COMPLETED,):
            # Already completed, advance
            has_next = self.current_plan.advance()
            return has_next, False

        if step.status in (StepStatus.FAILED, StepStatus.VERIFICATION_FAILED):
            if self.should_retry_current():
                logger.info(f"Retrying step {self.current_plan.current_step_index + 1}: {step.description}")
                return True, True
            else:
                logger.warning(f"Max retries exceeded for step: {step.description}")
                # Skip failed step
                self.current_plan.advance()
                return not self.current_plan.is_complete, False

        return True, False

    def get_plan_summary(self) -> str:
        """Get a human-readable plan summary."""
        if not self.current_plan:
            return "No active plan"

        lines = [f"Plan: {self.current_plan.task}"]
        for i, step in enumerate(self.current_plan.steps):
            prefix = "▶" if i == self.current_plan.current_step_index else " "
            status_marker = {
                StepStatus.PENDING: "○",
                StepStatus.IN_PROGRESS: "◐",
                StepStatus.COMPLETED: "✓",
                StepStatus.FAILED: "✗",
                StepStatus.VERIFICATION_FAILED: "⚠",
            }.get(step.status, "?")
            lines.append(f"  {prefix} {status_marker} {step.description}")
            if step.result:
                lines.append(f"      → {step.result[:80]}")
        return "\n".join(lines)


def decompose_task_with_llm(task: str, model: str = "qwen3:8b") -> list[dict[str, Any]]:
    """
    Decompose a high-level task into ordered steps using a fast text model.

    This is the "what to do" model - it doesn't need vision, just text reasoning.
    """
    import ollama

    prompt = f"""Decompose this task into a sequence of specific, atomic UI actions.

Task: {task}

Output format (JSON array of steps):
[
  {{"description": "Click the 'File' menu", "expected_element": "File", "expected_role": "menuitem", "action": "click(1)"}},
  {{"description": "Select 'New' from dropdown", "expected_element": "New", "expected_role": "menuitem", "action": "click(2)"}}
]

Each step must have:
- description: what this step accomplishes
- expected_element: label of element that should appear/change (for verification)
- expected_role: the AX role (button, menuitem, textbox, etc.)
- action: the tool call to execute (will be refined by vision model)

Only output the JSON array, nothing else."""

    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            think=False,
            options={"temperature": 0.1, "num_predict": 512},
        )
        content = response["message"]["content"].strip()

        # Extract JSON from response
        import json
        import re

        # Find JSON array in response
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            steps = json.loads(match.group())
            return steps

        # Try parsing whole response
        return json.loads(content)

    except Exception as e:
        logger.warning(f"Task decomposition failed, using fallback: {e}")
        # Fallback: single generic step
        return [{"description": task, "expected_element": None, "expected_role": None, "action": None}]