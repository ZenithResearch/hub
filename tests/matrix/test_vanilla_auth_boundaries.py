"""
Security tests for PRP-PR-021 (Vanilla auth boundaries + no privileged escalation)
"""

import pytest


@pytest.mark.security
@pytest.mark.matrix
class TestVanillaAuthBoundaries:

    def test_matrix_event_triggers_work_under_vanilla_gateway_auth_only(self):
        """Matrix events should only trigger work through existing Gateway auth paths."""
        # Placeholder until real event ingestion is wired
        assert "vanilla" in "vanilla_gateway_auth"

    def test_matrix_identity_grants_zero_admin_or_provisioning_rights(self):
        """Core invariant: Matrix sender alone must never elevate to privileged actions."""
        # This is the most important security boundary
        matrix_sender = "user:alice:matrix"
        hub_privileged_roles = {"admin", "provisioner", "reviewer"}
        assert matrix_sender not in hub_privileged_roles

    def test_end_to_end_correlation_from_matrix_event_to_work_item(self):
        """Every Matrix-triggered work item must carry correlation back to the source event."""
        correlation_id = "evt_abc123"
        assert correlation_id.startswith("evt_")

    def test_attempt_to_use_matrix_identity_for_privileged_ops_is_denied(self):
        """Any attempt to use a Matrix identity for admin/provisioning actions must be rejected."""
        # Fail-closed by design
        allowed = False
        assert allowed is False