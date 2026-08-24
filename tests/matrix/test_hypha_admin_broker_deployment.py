from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_broker_image_is_digest_pinned_and_routed_only_by_caddy():
    variables = read("infra/matrix/aws/variables.tf")
    main = read("infra/matrix/aws/main.tf")
    user_data = read("infra/matrix/aws/user_data.sh.tpl")

    assert 'variable "admin_broker_image"' in variables
    assert "@sha256:" in variables[variables.index('variable "admin_broker_image"') :]
    assert "admin_broker_image      = var.admin_broker_image" in main
    assert "hypha-admin-broker:8080" in user_data
    assert "handle /_hypha/admin/v1/*" in user_data
    assert "request_body {" in user_data
    assert "max_size 64KB" in user_data
    assert "reverse_proxy hypha-admin-broker:8080" in user_data
    assert '"8080:8080"' not in user_data
    assert '"8008:8008"' not in user_data


def test_broker_container_is_internal_non_root_read_only_and_unprivileged():
    user_data = read("infra/matrix/aws/user_data.sh.tpl")

    for marker in [
        "hypha-admin-broker:",
        "user: \"65532:65532\"",
        "read_only: true",
        "cap_drop:",
        "- ALL",
        "no-new-privileges:true",
        "tmpfs:",
        "networks: [matrix-internal]",
    ]:
        assert marker in user_data
    broker_block = user_data.split("  hypha-admin-broker:", 1)[1].split("  caddy:", 1)[0]
    assert "ports:" not in broker_block
    assert "/var/run/docker.sock" not in broker_block
    assert "/opt/matrix-data" not in broker_block
    assert "matrix-db" not in broker_block


def test_broker_runtime_secret_schema_uses_verifier_and_server_only_service_credential():
    user_data = read("infra/matrix/aws/user_data.sh.tpl")
    population = read("scripts/populate_fresh_synapse_secret.py")

    for marker in [
        '"HYPHA_ADMIN_BROKER_SECRET_VERIFIER"',
        '"HYPHA_ADMIN_BROKER_SERVICE_PASSWORD"',
    ]:
        assert marker in user_data
        assert marker in population
    assert "HYPHA_ADMIN_BROKER_SECRET=" not in user_data
    assert "HYPHA_ADMIN_BROKER_ACCESS_TOKEN" not in user_data


def test_broker_image_refreshes_its_base_and_keeps_unfixed_findings_visible():
    dockerfile = read("services/hypha_admin_broker/Dockerfile")
    workflow = read(".github/workflows/hypha-admin-broker-image.yml")

    base = (
        "python:3.12.14-slim-bookworm@sha256:"
        "a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134"
    )
    assert dockerfile.count(base) == 2
    assert workflow.count("Report all high or critical vulnerabilities") == 1
    assert workflow.count("ignore-unfixed: false") == 2
    assert workflow.count("ignore-unfixed: true") == 2
    assert workflow.count('exit-code: "0"') == 2
    assert workflow.count('exit-code: "1"') == 2


def test_broker_image_publication_is_bound_to_the_exact_hypha_account_and_repository():
    workflow = read(".github/workflows/hypha-admin-broker-image.yml")
    bootstrap = read("infra/matrix/aws/bootstrap.yaml")

    for marker in [
        'AWS_ACCOUNT_ID: "610992396917"',
        "AWS_ROLE_ARN: arn:aws:iam::610992396917:role/HyphaAdminBrokerImagePublisherRole",
        "ECR_REPOSITORY: hypha-admin-broker",
        '[[ "$ACTUAL_ACCOUNT_ID" = "$AWS_ACCOUNT_ID" ]]',
        "RepositoryName: hypha-admin-broker",
        "ImageTagMutability: IMMUTABLE",
        "Type: AWS::IAM::OIDCProvider",
        "repo:ZenithResearch/hub:environment:production",
        "ecr:PutImage",
    ]:
        assert marker in workflow or marker in bootstrap
    assert "AWS_PROD_DEPLOY_ROLE_ARN" not in workflow
    assert "zenith-hub-prod-runtime-grpc" not in workflow


def test_broker_private_ecr_pull_uses_ephemeral_auth_and_exact_host_permissions():
    bootstrap = read("infra/matrix/aws/bootstrap.yaml")
    runtime = read("infra/matrix/aws/main.tf")
    user_data = read("infra/matrix/aws/user_data.sh.tpl")
    deployer = read("scripts/deploy_hypha_admin_broker.py")

    for policy in [bootstrap, runtime]:
        assert "ecr:GetAuthorizationToken" in policy
        assert "ecr:BatchGetImage" in policy
        assert "ecr:GetDownloadUrlForLayer" in policy
    for script in [user_data, deployer]:
        assert "aws ecr get-login-password" in script
        assert "hypha-admin-broker-docker." in script
        assert 'export DOCKER_CONFIG="$' in script
        assert script.index("aws ecr get-login-password") < script.index("docker pull")
    assert 'EXPECTED_REGISTRY = "610992396917.dkr.ecr.us-east-1.amazonaws.com"' in deployer
    assert 'EXPECTED_REPOSITORY = "hypha-admin-broker"' in deployer


def test_existing_instance_is_updated_by_reviewed_ssm_deployer_with_rollback():
    main = read("infra/matrix/aws/main.tf")
    deployer = read("scripts/deploy_hypha_admin_broker.py")

    assert "ignore_changes = [user_data]" in main
    for marker in [
        "AWS-RunShellScript",
        "HyphaSynapseDeploymentRole",
        "sha256",
        "docker compose",
        "Caddyfile",
        "compose.yaml",
        "rollback",
        "backup",
        "hypha-admin-broker",
        "/_hypha/admin/v1/ready",
        "trap - ERR",
    ]:
        assert marker in deployer
    assert deployer.index("/_hypha/admin/v1/ready") < deployer.index('"trap - ERR"')
    for forbidden in [
        "--secret-value",
        "HYPHA_ADMIN_BROKER_SECRET=",
        "HYPHA_ADMIN_BROKER_SERVICE_PASSWORD=",
    ]:
        assert forbidden not in deployer


def test_broker_image_runs_as_distroless_non_root_entrypoint():
    dockerfile = read("services/hypha_admin_broker/Dockerfile")

    assert "USER 65532:65532" in dockerfile
    assert "COPY --from=" in dockerfile
    assert "services.hypha_admin_broker.main" in dockerfile
    assert "HEALTHCHECK" in dockerfile
