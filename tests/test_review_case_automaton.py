import unittest

from services.frank.review_case_automaton import (
    AUTOMATON_TO_GATEWAY_STATUS,
    MAX_FIX_ATTEMPTS,
    REVIEW_SCOPE_FULL,
    ProcessStepRef,
    require_full_review_scope,
    resolve_resume_boundary,
    transition,
)


class ReviewCaseAutomatonTests(unittest.TestCase):
    def test_core_transitions_return_status_and_reason(self):
        cases = [
            ("queued", "prepare", "ready", "case_ready"),
            ("ready", "start_processing", "processing", "processing_started"),
            ("processing", "processing_done", "review", "review_started"),
            ("processing", "packet_failed", "failed", "packet_failed"),
            ("processing", "pipeline_failed", "failed", "pipeline_failed"),
            ("failed", "retry_requested", "queued", "retry_requested"),
            ("succeeded", "rerun_requested", "queued", "rerun_requested"),
        ]

        for status, event, expected_status, expected_reason in cases:
            with self.subTest(status=status, event=event):
                result = transition(status, event)
                self.assertEqual(result.status, expected_status)
                self.assertEqual(result.status_reason, expected_reason)
                self.assertIsNone(result.review_scope)

    def test_review_passed_carries_full_review_scope(self):
        result = transition("review", "review_passed")

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.status_reason, "review_passed")
        self.assertEqual(result.review_scope, REVIEW_SCOPE_FULL)

    def test_review_failed_terminal_carries_full_review_scope(self):
        result = transition("review", "review_failed_terminal")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.status_reason, "review_failed")
        self.assertEqual(result.review_scope, REVIEW_SCOPE_FULL)

    def test_gateway_compatibility_mapping_preserves_public_statuses(self):
        self.assertEqual(
            AUTOMATON_TO_GATEWAY_STATUS,
            {
                "queued": "queued",
                "ready": "processing",
                "processing": "processing",
                "review": "processing",
                "succeeded": "processed",
                "failed": "failed",
            },
        )

    def test_invalid_transition_raises_value_error(self):
        with self.assertRaises(ValueError):
            transition("queued", "review_passed")

    def test_require_full_review_scope_accepts_only_exact_scope(self):
        self.assertEqual(require_full_review_scope(REVIEW_SCOPE_FULL), REVIEW_SCOPE_FULL)

        for scope in (None, "", "latest_fix_only"):
            with self.subTest(scope=scope):
                with self.assertRaises(ValueError):
                    require_full_review_scope(scope)

    def test_review_failed_fixable_is_frank_internal_only(self):
        with self.assertRaises(ValueError):
            transition(
                "review",
                "review_failed_fixable",
                resume_step_index=3,
                frank_internal=False,
            )

    def test_review_failed_fixable_requires_resume_step_index(self):
        with self.assertRaises(ValueError):
            transition("review", "review_failed_fixable", frank_internal=True)

    def test_review_failed_fixable_increments_attempt_and_preserves_resume_metadata(self):
        result = transition(
            "review",
            "review_failed_fixable",
            fix_attempt_count=0,
            resume_step_index=3,
            process_steps=[ProcessStepRef(step_index=3)],
            frank_internal=True,
        )

        self.assertEqual(result.status, "processing")
        self.assertEqual(result.status_reason, "fix_required")
        self.assertEqual(result.review_scope, REVIEW_SCOPE_FULL)
        self.assertEqual(result.fix_attempt_count, 1)
        self.assertEqual(result.resume_step_index, 3)
        self.assertIsNone(result.effective_resume_parent_index)
        self.assertEqual(result.rerun_step_indexes, (3,))

    def test_review_failed_fixable_allows_second_attempt(self):
        result = transition(
            "review",
            "review_failed_fixable",
            fix_attempt_count=1,
            resume_step_index=4,
            frank_internal=True,
        )

        self.assertEqual(result.status, "processing")
        self.assertEqual(result.status_reason, "fix_required")
        self.assertEqual(result.fix_attempt_count, MAX_FIX_ATTEMPTS)
        self.assertEqual(result.resume_step_index, 4)
        self.assertEqual(result.rerun_step_indexes, (4,))
        self.assertEqual(result.review_scope, REVIEW_SCOPE_FULL)

    def test_review_failed_fixable_fails_terminally_at_max_attempts_without_increment(self):
        result = transition(
            "review",
            "review_failed_fixable",
            fix_attempt_count=MAX_FIX_ATTEMPTS,
            resume_step_index=5,
            frank_internal=True,
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.status_reason, "review_failed")
        self.assertEqual(result.review_scope, REVIEW_SCOPE_FULL)
        self.assertEqual(result.fix_attempt_count, MAX_FIX_ATTEMPTS)
        self.assertIsNone(result.resume_step_index)
        self.assertEqual(result.rerun_step_indexes, ())

    def test_resolve_resume_boundary_reruns_concurrent_siblings_from_parent(self):
        process_steps = [
            ProcessStepRef(step_index=1),
            ProcessStepRef(step_index=2, parent_step_index=1, sibling_group_id="media"),
            ProcessStepRef(step_index=3, parent_step_index=1, sibling_group_id="media"),
            ProcessStepRef(step_index=4, parent_step_index=1, sibling_group_id="media"),
            ProcessStepRef(step_index=5, parent_step_index=1, sibling_group_id="other"),
        ]

        parent_index, rerun_indexes = resolve_resume_boundary(process_steps, 3)

        self.assertEqual(parent_index, 1)
        self.assertEqual(rerun_indexes, (2, 3, 4))

    def test_fixable_transition_includes_concurrent_sibling_boundary_metadata(self):
        process_steps = [
            ProcessStepRef(step_index=2, parent_step_index=1, sibling_group_id="media"),
            ProcessStepRef(step_index=3, parent_step_index=1, sibling_group_id="media"),
            ProcessStepRef(step_index=4, parent_step_index=1, sibling_group_id="media"),
        ]

        result = transition(
            "review",
            "review_failed_fixable",
            fix_attempt_count=0,
            resume_step_index=3,
            process_steps=process_steps,
            frank_internal=True,
        )

        self.assertEqual(result.status, "processing")
        self.assertEqual(result.status_reason, "fix_required")
        self.assertEqual(result.fix_attempt_count, 1)
        self.assertEqual(result.resume_step_index, 3)
        self.assertEqual(result.effective_resume_parent_index, 1)
        self.assertEqual(result.rerun_step_indexes, (2, 3, 4))
        self.assertEqual(result.review_scope, REVIEW_SCOPE_FULL)

    def test_resolve_resume_boundary_reruns_only_selected_step_without_sibling_group(self):
        process_steps = [
            ProcessStepRef(step_index=7, parent_step_index=1),
            ProcessStepRef(step_index=8, parent_step_index=1, sibling_group_id="media"),
        ]

        parent_index, rerun_indexes = resolve_resume_boundary(process_steps, 7)

        self.assertIsNone(parent_index)
        self.assertEqual(rerun_indexes, (7,))


if __name__ == "__main__":
    unittest.main()
