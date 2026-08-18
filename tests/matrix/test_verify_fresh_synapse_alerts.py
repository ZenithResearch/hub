from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[2]


def load_verifier():
    path = ROOT / "scripts/verify_fresh_synapse_alerts.py"
    spec = importlib.util.spec_from_file_location("verify_fresh_synapse_alerts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_budget_verifier_accepts_equivalent_aws_decimal_serializations(monkeypatch):
    verifier = load_verifier()

    for amount in ("30", "30.0", "30.00"):
        monkeypatch.setattr(
            verifier,
            "_run_aws",
            lambda arguments, amount=amount: (
                '{"Budget":{"BudgetName":"hypha-synapse-monthly",'
                '"BudgetLimit":{"Amount":"' + amount + '","Unit":"USD"},'
                '"BudgetType":"COST","TimeUnit":"MONTHLY"}}'
            ),
        )
        verifier._verify_budget()
