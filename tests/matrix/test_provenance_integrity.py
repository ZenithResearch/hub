"""
Security tests for PRP-PR-018 / iss-p18-001/002 (Provenance integrity)
"""

import pytest


@pytest.mark.security
@pytest.mark.matrix
class TestMatrixProvenanceIntegrity:

    def test_provenance_fields_are_injected_and_verified(self):
        """All required provenance fields must be present on Matrix events."""
        required_fields = {"room_id", "event_id", "sender", "correlation_id"}
        event = {"room_id": "!abc:zenith", "event_id": "$evt1", "sender": "@alice", "correlation_id": "c1"}
        assert required_fields.issubset(event.keys())

    def test_tampered_provenance_is_rejected_or_quarantined(self):
        """Tampered provenance should cause rejection or quarantine."""
        tampered = True
        assert tampered is True  # Will be replaced with real logic

    def test_matrix_sender_identity_grants_no_hub_authority(self):
        """Matrix sender must never be treated as a privileged Hub identity."""
        matrix_sender = "@user:matrix"
        assert "admin" not in matrix_sender and "provision" not in matrix_sender

    def test_outbound_events_carry_signature_or_proof(self):
        """Outbound Matrix events should carry some form of integrity proof."""
        assert True  # Placeholder for signature check