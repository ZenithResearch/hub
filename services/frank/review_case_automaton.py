from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

MAX_FIX_ATTEMPTS = 2
REVIEW_SCOPE_FULL = "full_output_against_objective_process_prompt_acceptance_criteria"

AUTOMATON_TO_GATEWAY_STATUS = {
    "queued": "queued",
    "ready": "processing",
    "processing": "processing",
    "review": "processing",
    "succeeded": "processed",
    "failed": "failed",
}


@dataclass(frozen=True)
class ProcessStepRef:
    step_index: int
    parent_step_index: int | None = None
    sibling_group_id: str | None = None
    retryable: bool = True


@dataclass(frozen=True)
class TransitionResult:
    status: str
    status_reason: str
    review_scope: str | None = None
    fix_attempt_count: int | None = None
    resume_step_index: int | None = None
    effective_resume_parent_index: int | None = None
    rerun_step_indexes: tuple[int, ...] = ()


_TRANSITIONS = {
    ("queued", "prepare"): TransitionResult("ready", "case_ready"),
    ("ready", "start_processing"): TransitionResult("processing", "processing_started"),
    ("processing", "processing_done"): TransitionResult("review", "review_started"),
    ("processing", "packet_failed"): TransitionResult("failed", "packet_failed"),
    ("processing", "pipeline_failed"): TransitionResult("failed", "pipeline_failed"),
    ("review", "review_passed"): TransitionResult(
        "succeeded",
        "review_passed",
        review_scope=REVIEW_SCOPE_FULL,
    ),
    ("review", "review_failed_terminal"): TransitionResult(
        "failed",
        "review_failed",
        review_scope=REVIEW_SCOPE_FULL,
    ),
    ("failed", "retry_requested"): TransitionResult("queued", "retry_requested"),
    ("succeeded", "rerun_requested"): TransitionResult("queued", "rerun_requested"),
}


def require_full_review_scope(scope: str | None) -> str:
    if scope != REVIEW_SCOPE_FULL:
        raise ValueError(f"review scope must be {REVIEW_SCOPE_FULL!r}")
    return scope


def resolve_resume_boundary(
    process_steps: Iterable[ProcessStepRef] | None,
    resume_step_index: int,
) -> tuple[int | None, tuple[int, ...]]:
    if process_steps is None:
        return None, (resume_step_index,)

    steps = tuple(process_steps)
    selected_step = next(
        (step for step in steps if step.step_index == resume_step_index),
        None,
    )
    if selected_step is None:
        raise ValueError(f"resume_step_index not found in process_steps: {resume_step_index}")

    if selected_step.parent_step_index is None or selected_step.sibling_group_id is None:
        return None, (resume_step_index,)

    rerun_step_indexes = tuple(
        step.step_index
        for step in steps
        if step.parent_step_index == selected_step.parent_step_index
        and step.sibling_group_id == selected_step.sibling_group_id
    )
    return selected_step.parent_step_index, rerun_step_indexes


def transition(
    status: str,
    event: str,
    *,
    fix_attempt_count: int = 0,
    resume_step_index: int | None = None,
    process_steps: Iterable[ProcessStepRef] | None = None,
    frank_internal: bool = False,
) -> TransitionResult:
    if event == "review_failed_fixable":
        return _transition_review_failed_fixable(
            status,
            fix_attempt_count=fix_attempt_count,
            resume_step_index=resume_step_index,
            process_steps=process_steps,
            frank_internal=frank_internal,
        )

    try:
        return _TRANSITIONS[(status, event)]
    except KeyError as exc:
        raise ValueError(f"invalid review case transition: {status} + {event}") from exc


def _transition_review_failed_fixable(
    status: str,
    *,
    fix_attempt_count: int,
    resume_step_index: int | None,
    process_steps: Iterable[ProcessStepRef] | None,
    frank_internal: bool,
) -> TransitionResult:
    if not frank_internal:
        raise ValueError("review_failed_fixable is Frank-internal only")
    if status != "review":
        raise ValueError(f"invalid review case transition: {status} + review_failed_fixable")
    if resume_step_index is None:
        raise ValueError("review_failed_fixable requires resume_step_index")
    if fix_attempt_count >= MAX_FIX_ATTEMPTS:
        return TransitionResult(
            "failed",
            "review_failed",
            review_scope=REVIEW_SCOPE_FULL,
            fix_attempt_count=fix_attempt_count,
        )

    effective_resume_parent_index, rerun_step_indexes = resolve_resume_boundary(
        process_steps,
        resume_step_index,
    )
    return TransitionResult(
        "processing",
        "fix_required",
        review_scope=REVIEW_SCOPE_FULL,
        fix_attempt_count=fix_attempt_count + 1,
        resume_step_index=resume_step_index,
        effective_resume_parent_index=effective_resume_parent_index,
        rerun_step_indexes=rerun_step_indexes,
    )
