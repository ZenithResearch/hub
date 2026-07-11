from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs/operations/matrix-static-landing-rollout.md"
ISSUE_CONTEXT = ROOT / "docs/issues/matrix-synapse-v0/issue-91-zenith-synapse-static.md"


def test_static_landing_rollout_is_digest_pinned_and_synapse_only():
    runbook = RUNBOOK.read_text()

    for required in [
        "Hardened Synapse Image",
        "workflow_dispatch",
        "Trivy",
        "imageDigest",
        "@sha256:",
        "matrix_synapse_image",
        "zenith-hub-prod-matrix-synapse",
        "terraform plan",
        "terraform apply",
    ]:
        assert required in runbook
    assert "only the Synapse task definition and ECS service" in runbook
    assert "no RDS, EFS, database, secret, Gateway, Frank, Cases, or Eventbus changes" in runbook


def test_static_landing_rollout_requires_live_page_api_federation_and_browser_smokes():
    runbook = RUNBOOK.read_text()

    for required in [
        "https://synapse.zenith-research.ca/_matrix/static/",
        "HTTP/2 200",
        "content-type: text/html",
        "Zenith Matrix is running",
        "/_matrix/client/versions",
        "/_matrix/federation/v1/version",
        "320",
        "console",
        "rollback",
    ]:
        assert required in runbook


def test_issue_context_records_scope_provenance_and_unexecuted_rollout_boundary():
    context = ISSUE_CONTEXT.read_text()

    assert "https://github.com/ZenithResearch/hub/issues/91" in context
    assert "element-hq/synapse v1.156.0" in context
    assert "ef574605200dd568e97dac7d90995ca43620a5f8" in context
    assert "synapse/static/index.html" in context
    assert "6d85132c52f1dc9021e124093f20164a64a89dff" in context
    assert "not deployed" in context
    assert "Matrix identity" in context and "Hub authority" in context
