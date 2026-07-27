import importlib.machinery
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
IAC = ROOT / "infra/aws_baseline_80"


def test_cloud_agent_node_has_no_ingress_or_public_ip() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")

    security_group = terraform.split(
        'resource "aws_security_group" "hermes_cloud_agent" {', 1
    )[1].split('resource "aws_iam_role" "hermes_cloud_agent" {', 1)[0]
    instance = terraform.split('resource "aws_instance" "hermes_cloud_agent" {', 1)[1]

    assert "ingress {" not in security_group
    assert "associate_public_ip_address = false" in instance
    assert 'http_tokens   = "required"' in instance
    assert 'http_endpoint = "enabled"' in instance


def test_cloud_agent_uses_ssm_and_encrypted_persistent_storage() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")

    assert "AmazonSSMManagedInstanceCore" in terraform
    assert 'resource "aws_ebs_volume" "hermes_cloud_agent_state"' in terraform
    assert re.search(r"encrypted\s+= true", terraform)
    assert 'resource "aws_volume_attachment" "hermes_cloud_agent_state"' in terraform
    assert 'device_name = "/dev/sdf"' in terraform


def test_cloud_agent_is_disabled_and_incomplete_configuration_fails_closed() -> None:
    variables = (IAC / "variables.tf").read_text(encoding="utf-8")
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")
    tfvars_example = (IAC / "terraform.tfvars.example").read_text(encoding="utf-8")

    assert 'variable "enable_hermes_cloud_agent"' in variables
    assert "default     = false" in variables
    assert 'variable "hermes_cloud_agent_ami_id"' in variables
    assert 'condition     = var.hermes_cloud_agent_ami_id != ""' in terraform
    assert 'variable "hermes_cloud_agent_secret_arns"' in variables
    assert 'condition     = var.hermes_cloud_agent_matrix_secret_arn != ""' in terraform
    assert "var.hermes_cloud_agent_matrix_secret_arn" in terraform
    assert "count = var.enable_hermes_cloud_agent ? 1 : 0" in terraform
    assert "enable_hermes_cloud_agent = false" in tfvars_example


def test_cloud_agent_profile_is_bound_to_the_exact_artifact_lock() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")
    variables = (IAC / "variables.tf").read_text(encoding="utf-8")
    cloud_agent_variables = variables.split(
        'variable "enable_hermes_cloud_agent"', 1
    )[1]

    assert re.search(
        r'local_inference_lock\s+= jsondecode\(file\("\$\{path\.module\}/\.\./hermes_cloud_agent/artifacts/local-inference\.lock\.json"\)\)',
        terraform,
    )
    assert re.search(
        r'local_inference_lock_sha256\s+= filesha256\("\$\{path\.module\}/\.\./hermes_cloud_agent/artifacts/local-inference\.lock\.json"\)',
        terraform,
    )
    assert re.search(
        r"model_id\s+= local\.local_inference_lock\.desired\.model\.model_id",
        terraform,
    )
    assert re.search(
        r"model_sha256\s+= local\.local_inference_lock\.desired\.model\.sha256",
        terraform,
    )
    assert re.search(
        r"artifact_lock_sha256\s+= local\.local_inference_lock_sha256", terraform
    )
    assert "local.local_inference_lock.desired.llama_cpp.s3_version_id" in terraform
    assert "local.local_inference_lock.desired.model.s3_version_id" in terraform
    assert terraform.count('s3_version_id != "latest"') == 2
    assert "qwen3.5" not in cloud_agent_variables.lower()


def test_cloud_agent_prepares_only_exact_s3_artifact_versions() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")
    bootstrap = (IAC.parent / "hermes_cloud_agent/bootstrap.sh.tftpl").read_text(
        encoding="utf-8"
    )
    prepare_service = (
        IAC.parent / "hermes_cloud_agent/systemd/hermes-inference-prepare.service"
    ).read_text(encoding="utf-8")
    gateway_service = (
        IAC.parent / "hermes_cloud_agent/systemd/hermes-cloud-agent.service"
    ).read_text(encoding="utf-8")
    assert 'actions   = ["s3:GetObjectVersion"]' in terraform
    assert terraform.count('variable = "s3:VersionId"') == 2
    assert "local.local_inference_lock.desired.llama_cpp.s3_key" in terraform
    assert "local.local_inference_lock.desired.model.s3_key" in terraform
    assert '"s3:GetObject"' not in terraform
    assert '"s3:ListBucket"' not in terraform
    for template_value in (
        "inference_lock_b64",
        "inference_lock_schema_b64",
        "inference_preparer_b64",
        "inference_prepare_service_b64",
    ):
        assert template_value in terraform
        assert f"${{{template_value}}}" in bootstrap
    assert "hermes-prepare-local-inference" in bootstrap
    assert "hermes-inference-prepare.service" in bootstrap
    assert "Before=hermes-inference.service hermes-cloud-agent.service" in prepare_service
    assert "Requires=hermes-state-volume.service" in prepare_service
    assert (
        "Requires=hermes-state-volume.service hermes-podman.service "
        "hermes-inference-prepare.service hermes-inference.service" in gateway_service
    )
    assert (
        "After=network-online.target hermes-state-volume.service "
        "hermes-podman.service hermes-inference-prepare.service "
        "hermes-inference.service" in gateway_service
    )
    assert "Type=oneshot" in prepare_service
    assert "RemainAfterExit=yes" in prepare_service
    assert "ExecStart=/opt/hermes/venv/bin/python /usr/local/libexec/hermes-prepare-local-inference" in prepare_service
    assert "NoNewPrivileges=true" in prepare_service
    assert "CapabilityBoundingSet=CAP_CHOWN" in prepare_service
    assert "AmbientCapabilities=CAP_CHOWN" in prepare_service
    assert "ProtectSystem=strict" in prepare_service
    assert "ReadWritePaths=/opt/hermes/inference /var/lib/hermes/models /var/lib/hermes/inference" in prepare_service


def test_cloud_agent_iam_secret_access_is_explicitly_scoped() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")

    assert 'actions   = ["secretsmanager:GetSecretValue"]' in terraform
    assert "resources = [var.hermes_cloud_agent_matrix_secret_arn]" in terraform
    assert 'actions   = ["kms:Decrypt"]' in terraform
    assert "resources = var.hermes_cloud_agent_secret_kms_key_arns" in terraform
    assert "secretsmanager:*" not in terraform
    assert 'resources = ["*"]' not in terraform


def test_local_inference_is_supervised_as_a_loopback_only_separate_identity() -> None:
    root = IAC.parent / "hermes_cloud_agent"
    bootstrap = (root / "bootstrap.sh.tftpl").read_text(encoding="utf-8")
    service_path = root / "systemd/hermes-inference.service"
    supervisor_path = root / "runtime/hermes-inference-supervisor"
    preflight_path = root / "runtime/hermes-inference-network-preflight"
    assert service_path.is_file(), "C4.3 inference service is not implemented"
    assert supervisor_path.is_file(), "C4.3 inference supervisor is not implemented"
    assert preflight_path.is_file(), "C4.3 network preflight is not implemented"
    service = service_path.read_text(encoding="utf-8")
    supervisor = supervisor_path.read_text(encoding="utf-8")
    preflight = preflight_path.read_text(encoding="utf-8")

    assert "User=hermes-inference" in service
    assert "Group=hermes-inference" in service
    assert "SupplementaryGroups=" in service
    assert "Type=notify" in service
    assert "NotifyAccess=main" in service
    assert "WatchdogSec=90s" in service
    assert "Upholds=hermes-cloud-agent.service" in service
    assert "ExecStartPre=+/usr/local/libexec/hermes-inference-network-preflight" in service
    assert "ExecStart=/opt/hermes/venv/bin/python /usr/local/libexec/hermes-inference-supervisor" in service
    assert "IPAddressDeny=any" in service
    assert "IPAddressAllow=localhost" in service
    assert "SocketBindDeny=any" in service
    assert "SocketBindAllow=tcp:ipv4:8080" in service
    assert "RestrictAddressFamilies=AF_UNIX AF_INET" in service
    assert "MemoryHigh=20G" in service
    assert "MemoryMax=24G" in service
    assert "CPUQuota=750%" in service
    assert "TasksMax=128" in service
    assert "LimitNOFILE=4096" in service
    assert "LimitCORE=0" in service
    assert "InaccessiblePaths=/etc/hermes-cloud-agent/profile.json" in service
    assert "/var/lib/hermes/profiles" in service
    assert "/run/hermes-podman" in service
    assert "0.0.0.0" not in supervisor
    assert "--model-url" not in supervisor
    assert 'BASE_URL = "http://127.0.0.1:8080"' in supervisor

    assert "bpftool" in bootstrap
    assert "CONFIG_CGROUP_BPF is set to y" in preflight
    assert "eBPF program_type cgroup_skb is available" in preflight
    assert "eBPF program_type cgroup_sock_addr is available" in preflight
    assert "nftables" in bootstrap
    assert (
        "meta skuid $inference_uid ct direction reply ct original ip daddr "
        "127.0.0.1 meta l4proto tcp ct original proto-dst 8080 accept" in preflight
    )
    assert "ct state established,related accept" not in preflight
    assert "meta skuid $inference_uid ip daddr 127.0.0.1 tcp dport 8080 accept" in preflight
    assert "meta skuid $inference_uid reject" in preflight
    assert "udp dport 53 accept" not in preflight
    assert "useradd --system --gid hermes-inference" in bootstrap
    assert "usermod --append --groups hermes-podman hermes-inference" not in bootstrap
    assert "install -d -m 0751 -o root -g root /etc/hermes-cloud-agent" in bootstrap
    assert "chown root:hermes-inference /etc/hermes-cloud-agent/local-inference.lock.json" in bootstrap
    assert "/etc/hermes-cloud-agent/local-inference-lock.schema.json" in bootstrap
    assert "chmod 0440 /etc/hermes-cloud-agent/local-inference.lock.json" in bootstrap
    assert "chown root:hermes /etc/hermes-cloud-agent/profile.json" in bootstrap


def test_gateway_waits_for_exact_inference_readiness_not_a_bare_path() -> None:
    root = IAC.parent / "hermes_cloud_agent"
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")
    bootstrap = (root / "bootstrap.sh.tftpl").read_text(encoding="utf-8")
    gateway_service = (root / "systemd/hermes-cloud-agent.service").read_text(
        encoding="utf-8"
    )
    prepare_service = (root / "systemd/hermes-inference-prepare.service").read_text(
        encoding="utf-8"
    )

    for template_value in (
        "inference_supervisor_b64",
        "inference_network_preflight_b64",
        "inference_service_b64",
    ):
        assert template_value in terraform
        assert f"${{{template_value}}}" in bootstrap
    assert "hermes-inference.service" in bootstrap
    enable_command = bootstrap.split("systemctl enable", 1)[1].split(
        "systemctl start", 1
    )[0]
    assert "hermes-inference.service" not in enable_command
    assert "hermes-inference.service" in gateway_service
    assert "BindsTo=hermes-inference.service" in gateway_service
    assert "ConditionPathExists=/var/lib/hermes/inference/READY.json" not in gateway_service
    assert "Before=hermes-inference.service hermes-cloud-agent.service" in prepare_service


def test_cloud_agent_bootstrap_pins_hermes_and_materializes_profile_service() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")
    bootstrap = (IAC.parent / "hermes_cloud_agent/bootstrap.sh.tftpl").read_text(
        encoding="utf-8"
    )
    runner = (IAC.parent / "hermes_cloud_agent/runtime/hermes-cloud-agent-run").read_text(
        encoding="utf-8"
    )
    service = (
        IAC.parent / "hermes_cloud_agent/systemd/hermes-cloud-agent.service"
    ).read_text(encoding="utf-8")

    assert "user_data_base64" in terraform
    assert "templatefile(" in terraform
    assert "hermes_cloud_agent/bootstrap.sh.tftpl" in terraform
    assert "3ef6bbd201263d354fd83ec55b3c306ded2eb72a" in bootstrap
    assert 'install ".[matrix]"' in bootstrap
    assert "HERMES_HOME" in runner
    assert "MATRIX_E2EE_MODE=required" in runner
    assert "MATRIX_ALLOWED_USERS" in runner
    assert "MATRIX_ALLOWED_ROOMS" in runner
    assert "MATRIX_SESSION_SCOPE=room" in runner
    assert "API_SERVER_ENABLED=false" in runner
    assert "WEBHOOK_ENABLED=false" in runner
    assert "TERMINAL_ENV=docker" in runner
    assert "gateway run --external-supervisor" in runner
    assert "ConditionPathExists=/var/lib/hermes/inference/READY.json" not in service
    assert "BindsTo=hermes-inference.service" in service
    assert "NoNewPrivileges=true" in service


def test_cloud_agent_runtime_fetches_secret_without_persisting_raw_value() -> None:
    runner = (IAC.parent / "hermes_cloud_agent/runtime/hermes-cloud-agent-run").read_text(
        encoding="utf-8"
    )
    secret_reader = (
        IAC.parent / "hermes_cloud_agent/runtime/hermes-read-matrix-secret"
    ).read_text(encoding="utf-8")

    assert "hermes-read-matrix-secret" in runner
    assert 'boto3.client("secretsmanager")' in secret_reader
    assert "get_secret_value(SecretId=secret_arn)" in secret_reader
    assert "SecretBinary" not in secret_reader
    assert "aws secretsmanager" not in runner
    assert "MATRIX_ACCESS_TOKEN" in runner
    assert "MATRIX_DEVICE_ID" in runner
    assert "MATRIX_RECOVERY_KEY" not in runner
    assert "/etc/hermes-cloud-agent/profile.json" in runner
    assert ">>" not in runner
    assert ".env" not in runner
    assert "set -x" not in runner


def test_cloud_agent_uses_rootless_podman_socket_for_docker_backend() -> None:
    bootstrap = (IAC.parent / "hermes_cloud_agent/bootstrap.sh.tftpl").read_text(
        encoding="utf-8"
    )
    runner = (IAC.parent / "hermes_cloud_agent/runtime/hermes-cloud-agent-run").read_text(
        encoding="utf-8"
    )
    podman_service = (
        IAC.parent / "hermes_cloud_agent/systemd/hermes-podman.service"
    ).read_text(encoding="utf-8")

    assert "podman" in bootstrap
    assert "DOCKER_HOST=unix:///run/hermes-podman/podman.sock" in runner
    assert "User=hermes-sandbox" in podman_service
    assert "Group=hermes-podman" in podman_service
    assert "podman system service" in podman_service
    assert "Delegate=yes" in podman_service
    assert "Environment=HOME=/var/lib/hermes/podman" in podman_service
    assert 'export HOME="$profile_home"' in runner
    assert "usermod -aG docker" not in bootstrap
    assert "groupadd --system hermes-podman" in bootstrap
    assert "hermes-sandbox" in bootstrap
    assert "InaccessiblePaths=/var/lib/hermes/profiles" in podman_service
    assert "ProtectProc=invisible" in podman_service
    assert "docker.sock" not in runner


def test_cloud_agent_state_mount_waits_for_declared_encrypted_volume() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")
    mount_script = (
        IAC.parent / "hermes_cloud_agent/runtime/hermes-state-volume-mount"
    ).read_text(encoding="utf-8")

    assert re.search(
        r"availability_zone\s+= aws_subnet\.private\[0\]\.availability_zone",
        terraform,
    )
    assert "state_volume_id" in terraform
    assert "nvme-Amazon_Elastic_Block_Store" in mount_script
    assert "blkid" in mount_script
    assert "mkfs.ext4" in mount_script
    assert "/var/lib/hermes" in mount_script


def test_matrix_crypto_state_is_owner_only_and_clone_activation_fails_closed() -> None:
    mount_script = (
        IAC.parent / "hermes_cloud_agent/runtime/hermes-state-volume-mount"
    ).read_text(encoding="utf-8")
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")

    assert 'install -d -m 0700 -o hermes -g hermes' in mount_script
    assert 'install -m 0600 -o hermes -g hermes' in mount_script
    assert 'install -d -m 0711 -o root -g root "$MOUNT_POINT"' in mount_script
    assert 'chown root:root "$MOUNT_POINT"' in mount_script
    assert '"$MOUNT_POINT/podman"' in mount_script
    assert "-o hermes-sandbox -g hermes-podman" in mount_script
    assert ".active-instance" in mount_script
    assert "latest/api/token" in mount_script
    assert "refusing cloned or concurrently activated Matrix device state" in mount_script
    assert "multi_attach_enabled = false" in terraform
    assert "prevent_destroy = true" in terraform
    assert 'condition     = var.hermes_cloud_agent_state_kms_key_arn != ""' in terraform


def test_gateway_process_and_tool_sandbox_are_hardened_against_key_exfiltration() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")
    gateway_service = (
        IAC.parent / "hermes_cloud_agent/systemd/hermes-cloud-agent.service"
    ).read_text(encoding="utf-8")

    assert "UMask=0077" in gateway_service
    assert "LimitCORE=0" in gateway_service
    assert "LockPersonality=true" in gateway_service
    assert "RestrictSUIDSGID=true" in gateway_service
    assert "SystemCallArchitectures=native" in gateway_service
    assert "ProtectProc=invisible" in gateway_service
    assert "KeyringMode=private" in gateway_service
    assert re.search(r"docker_volumes\s+= \[\]", terraform)
    assert re.search(r"docker_forward_env\s+= \[\]", terraform)
    assert re.search(r"docker_env\s+= \{\}", terraform)
    assert re.search(r"docker_network\s+= false", terraform)
    assert re.search(r"docker_mount_cwd_to_workspace\s+= false", terraform)
    assert re.search(r"credential_files\s+= \[\]", terraform)
    assert re.search(r'env_type\s+= "docker"', terraform)
    assert "platform_toolsets" in terraform
    assert 'matrix = ["clarify", "file", "memory", "terminal", "todo"]' in terraform
    for disabled in ("skills", "delegation", "cronjob", "messaging", "browser"):
        assert f'"{disabled}"' in terraform


def test_matrix_secret_payload_rejects_recovery_key_and_unknown_fields(monkeypatch) -> None:
    script = IAC.parent / "hermes_cloud_agent/runtime/hermes-read-matrix-secret"
    fake_boto3 = SimpleNamespace(client=lambda _service: None)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    loader = importlib.machinery.SourceFileLoader("hermes_secret_reader_test", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    class FakeClient:
        def __init__(self, payload: dict[str, str]) -> None:
            self.payload = payload

        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            assert SecretId.startswith("arn:")
            return {"SecretString": json.dumps(self.payload)}

    normalized = module.load_matrix_secret(
        "arn:aws:secretsmanager:us-west-2:123456789012:secret:matrix",
        client=FakeClient({"access_token": "token", "device_id": "DEVICE"}),
    )
    assert json.loads(normalized) == {"access_token": "token", "device_id": "DEVICE"}

    with pytest.raises(ValueError, match="exactly access_token and device_id"):
        module.load_matrix_secret(
            "arn:aws:secretsmanager:us-west-2:123456789012:secret:matrix",
            client=FakeClient(
                {
                    "access_token": "token",
                    "device_id": "DEVICE",
                    "recovery_key": "must-not-enter-runtime",
                }
            ),
        )


def test_matrix_room_keys_are_shared_only_with_cross_signed_trusted_devices() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text(encoding="utf-8")
    bootstrap = (IAC.parent / "hermes_cloud_agent/bootstrap.sh.tftpl").read_text(
        encoding="utf-8"
    )
    trust_patch = (
        IAC.parent / "hermes_cloud_agent/patches/strict-matrix-device-trust.patch"
    ).read_text(encoding="utf-8")

    assert "matrix_trust_patch_b64" in terraform
    assert "apply --unidiff-zero --check /opt/hermes/strict-matrix-device-trust.patch" in bootstrap
    assert "apply --unidiff-zero /opt/hermes/strict-matrix-device-trust.patch" in bootstrap
    assert trust_patch.count("TrustState.CROSS_SIGNED_TRUSTED") == 2
    assert "olm.share_keys_min_trust = TrustState.UNVERIFIED" in trust_patch
    assert "olm.send_keys_min_trust = TrustState.UNVERIFIED" in trust_patch


def test_state_volume_identity_has_no_unsafe_device_name_fallback() -> None:
    mount_script = (ROOT / "infra/hermes_cloud_agent/runtime/hermes-state-volume-mount").read_text()

    assert "nvme-Amazon_Elastic_Block_Store_${volume_serial}" in mount_script
    assert "/dev/xvdf" not in mount_script
    assert "/dev/sdf" not in mount_script


def test_matrix_store_binding_includes_identity_and_whoami_is_verified() -> None:
    mount_script = (ROOT / "infra/hermes_cloud_agent/runtime/hermes-state-volume-mount").read_text()
    runner = (ROOT / "infra/hermes_cloud_agent/runtime/hermes-cloud-agent-run").read_text()

    for field in ("volume_id", "instance_id", "homeserver", "user_id", "device_id"):
        assert field in mount_script
    assert "/_matrix/client/v3/account/whoami" in runner
    assert '[[ "$whoami_user_id" == "$user_id" ]]' in runner
    assert '[[ "$whoami_device_id" == "$matrix_device_id" ]]' in runner


def test_matrix_secret_and_kms_permissions_are_context_bounded() -> None:
    terraform = (IAC / "hermes_cloud_agent.tf").read_text()
    variables = (IAC / "variables.tf").read_text()

    assert "resources = [var.hermes_cloud_agent_matrix_secret_arn]" in terraform
    assert 'variable = "kms:ViaService"' in terraform
    assert 'variable = "kms:EncryptionContext:SecretARN"' in terraform
    assert 'data "aws_kms_key" "hermes_cloud_agent_state"' in terraform
    assert 'key_manager == "CUSTOMER"' in terraform
    assert "Optional customer-managed KMS key ARN" not in variables


def test_matrix_allowed_rooms_apply_to_direct_messages() -> None:
    trust_patch = (
        ROOT / "infra/hermes_cloud_agent/patches/strict-matrix-device-trust.patch"
    ).read_text()

    assert "room_id not in self._allowed_rooms" in trust_patch
    assert "DMs are not exempt" in trust_patch
