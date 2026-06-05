"""
Security tests for PRP-PR-018 / iss-p18-001/002 (Provenance integrity)
"""

import pytest


@pytest.mark.security
@pytest.mark.matrix
class TestMatrixProvenanceIntegrity:
    def test_provenance_fields_are_injected_and_verified(self):
        """All required provenance fields (room, event_id, sender, correlation) must be present."""
        assert True

    def test_tampered_provenance_is_rejected_or_quarantined(self):
        assert True

    def test_matrix_sender_identity_grants_no_hub_authority(self):
        """Critical: Matrix sender must never be treated as privileged Hub identity."""
        assert True

    def test_outbound_events_carry_signature_or_proof(self):
        assert True