from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_alert_verifier_requires_assumed_role_and_exact_budget_subscription_schedules():
    script = (ROOT / "scripts/verify_fresh_synapse_alerts.py").read_text(encoding="utf-8")
    policy = (ROOT / "infra/matrix/aws/bootstrap.yaml").read_text(encoding="utf-8")

    for marker in [
        'EXPECTED_PROFILE = "zenith-hypha-synapse"',
        'EXPECTED_ACCOUNT = "610992396917"',
        'EXPECTED_REGION = "us-east-1"',
        "assumed-role/HyphaSynapseDeploymentRole/",
        'EXPECTED_BUDGET = "hypha-synapse-monthly"',
        'EXPECTED_TOPIC_ARN = "arn:aws:sns:us-east-1:610992396917:hypha-synapse-expiry-alerts"',
        "describe-budget",
        "list-subscriptions-by-topic",
        "PendingConfirmation",
        "get-schedule",
        "2026-12-19T20:08:42",
        "2027-01-18T20:08:42",
        "2027-02-03T20:08:42",
        "2027-02-10T20:08:42",
    ]:
        assert marker in script

    for marker in ["budgets:ViewBudget", "sns:ListSubscriptionsByTopic", "scheduler:GetSchedule"]:
        assert marker in policy
    for forbidden in ["print(endpoint", 'response.get("Endpoint")', "Subscriptions\"]"]:
        assert forbidden not in script
