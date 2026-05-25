import unittest

from services.frank.review_case_automaton import (
    AUTOMATON_TO_GATEWAY_STATUS,
    REVIEW_SCOPE_FULL,
    require_full_review_scope,
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
        invalid_cases = [
            ("queued", "review_passed"),
            ("review", "review_failed_fixable"),
            ("failed", "retry_requested"),
            ("succeeded", "rerun_requested"),
        ]
        for status, event in invalid_cases:
            with self.subTest(status=status, event=event):
                with self.assertRaises(ValueError):
                    transition(status, event)

    def test_require_full_review_scope_accepts_only_exact_scope(self):
        self.assertEqual(require_full_review_scope(REVIEW_SCOPE_FULL), REVIEW_SCOPE_FULL)

        for scope in (None, "", "latest_fix_only"):
            with self.subTest(scope=scope):
                with self.assertRaises(ValueError):
                    require_full_review_scope(scope)


if __name__ == "__main__":
    unittest.main()
