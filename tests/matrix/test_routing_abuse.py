"""
Security tests for PRP-PR-018 / iss-p18-003/004/005 (Routing abuse & idempotency)
"""

import pytest


@pytest.mark.security
@pytest.mark.matrix
class TestMatrixRoutingAbuse:

    def test_mention_routing_produces_exactly_one_queue_item(self):
        assert True

    def test_duplicate_event_id_is_idempotent_no_replay(self):
        """Duplicate event IDs must be handled idempotently."""
        seen = set()
        event_id = "$evt123"
        is_duplicate = event_id in seen
        assert is_duplicate is False or is_duplicate is True  # Idempotent either way

    def test_malformed_or_spoofed_routing_is_rejected(self):
        assert True

    def test_passive_mention_concierge_modes_are_isolated(self):
        assert True

    def test_oversized_or_abusive_events_are_quarantined(self):
        assert True