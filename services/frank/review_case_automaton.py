from __future__ import annotations

from dataclasses import dataclass

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
class TransitionResult:
    status: str
    status_reason: str
    review_scope: str | None = None


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
}


def require_full_review_scope(scope: str | None) -> str:
    if scope != REVIEW_SCOPE_FULL:
        raise ValueError(f"review scope must be {REVIEW_SCOPE_FULL!r}")
    return scope


def transition(status: str, event: str) -> TransitionResult:
    try:
        return _TRANSITIONS[(status, event)]
    except KeyError as exc:
        raise ValueError(f"invalid review case transition: {status} + {event}") from exc
