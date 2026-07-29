from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "infra/hermes_cloud_agent/profile.schema.json"
ARTIFACT_LOCK_PATH = ROOT / "infra/hermes_cloud_agent/artifacts/local-inference.lock.json"
ARTIFACT_LOCK_SCHEMA_PATH = (
    ROOT / "infra/hermes_cloud_agent/artifacts/local-inference-lock.schema.json"
)
ISSUE_SPEC_PATH = ROOT / "docs/issues/hermes-cloud-agent-v0/issue-97-matrix-only-profiled-agent.md"
INFERENCE_RUNBOOK_PATH = (
    ROOT / "docs/issues/hermes-cloud-agent-v0/local-inference-operator-runbook.md"
)
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
PYPROJECT_PATH = ROOT / "pyproject.toml"
UV_LOCK_PATH = ROOT / "uv.lock"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _valid_config() -> dict:
    return {
        "schema_version": 1,
        "profile": {
            "id": "cloudproof",
            "home": "/var/lib/hermes/profiles/cloudproof",
        },
        "matrix": {
            "homeserver": "https://synapse.zenith-research.ca",
            "user_id": "@cloudproof:zenith-research.ca",
            "access_token_secret_ref": (
                "aws-secretsmanager:arn:aws:secretsmanager:us-west-2:123456789012:"
                "secret:hermes/cloudproof/matrix-token-AbCdEf"
            ),
            "crypto_store": "/var/lib/hermes/profiles/cloudproof/platforms/matrix/store",
            "e2ee_mode": "required",
            "allowed_users": ["@operator:zenith-research.ca"],
            "allowed_rooms": ["!proof:zenith-research.ca"],
            "session_scope": "room",
        },
        "gateway": {"api_server_enabled": False},
        "inference": {
            "provider": "custom",
            "base_url": "http://127.0.0.1:8080/v1",
            "model_id": "qwen3-8b-q4-k-m",
            "model_sha256": "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785",
            "artifact_lock_sha256": "a" * 64,
            "fallbacks": [],
        },
        "sandbox": {
            "backend": "docker",
            "network": False,
            "host_mounts": False,
            "credential_passthrough": False,
            "allowed_toolsets": ["clarify", "file", "memory", "terminal", "todo"],
        },
        "storage": {"encrypted": True},
        "operations": {
            "administration": "ssm",
            "public_ssh": False,
            "public_agent_ingress": False,
        },
    }


def test_schema_accepts_matrix_only_local_inference_contract() -> None:
    jsonschema.Draft202012Validator.check_schema(_schema())
    jsonschema.validate(_valid_config(), _schema())


def test_local_inference_lock_pins_exact_staged_artifacts() -> None:
    assert ARTIFACT_LOCK_PATH.is_file()
    assert ARTIFACT_LOCK_SCHEMA_PATH.is_file()
    lock_schema = json.loads(ARTIFACT_LOCK_SCHEMA_PATH.read_text(encoding="utf-8"))
    lock = json.loads(ARTIFACT_LOCK_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(lock_schema)
    jsonschema.validate(lock, lock_schema)

    desired = lock["desired"]
    assert "rollback" not in lock
    assert desired["llama_cpp"] == {
        "repository": "https://github.com/ggml-org/llama.cpp",
        "commit": "47a39665e7081dc482feec169961acc09750a5c4",
        "release": "b10000",
        "archive_filename": "llama-b10000-bin-ubuntu-x64.tar.gz",
        "archive_sha256": "80faa4e10350436aeb09f01c3f299f6ebeaf3000f21cdf2b0ec4d2299b056274",
        "size_bytes": 15855212,
        "s3_bucket": "zenith-hub-prod-llama-models-044528206149-us-east-1",
        "s3_key": "hermes-cloud-agent/runtime/llama.cpp/b10000/80faa4e10350436aeb09f01c3f299f6ebeaf3000f21cdf2b0ec4d2299b056274/llama-b10000-bin-ubuntu-x64.tar.gz",
        "s3_version_id": "apAEVDVfFYNUu13eZw0gIIaKYZwweTL5",
    }
    assert desired["model"] == {
        "source_repository": "https://huggingface.co/Qwen/Qwen3-8B-GGUF",
        "revision": "7c41481f57cb95916b40956ab2f0b139b296d974",
        "filename": "Qwen3-8B-Q4_K_M.gguf",
        "model_id": "qwen3-8b-q4-k-m",
        "sha256": "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785",
        "size_bytes": 5027783488,
        "s3_bucket": "zenith-hub-prod-llama-models-044528206149-us-east-1",
        "s3_key": "hermes-cloud-agent/models/Qwen3-8B/7c41481f57cb95916b40956ab2f0b139b296d974/d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785/Qwen3-8B-Q4_K_M.gguf",
        "s3_version_id": "pNL9.QCQfHICIn7qYcL5kA5WRSBStpuo",
        "license": "apache-2.0",
        "context_length": 32768,
        "chat_template": "jinja",
        "tool_calling_verified": False,
    }


@pytest.mark.parametrize(
    ("component", "field", "value"),
    [
        ("llama_cpp", "release", "latest"),
        ("llama_cpp", "archive_sha256", "A" * 64),
        ("llama_cpp", "s3_version_id", "null"),
        ("llama_cpp", "s3_version_id", "latest"),
        ("llama_cpp", "s3_key", "hermes-cloud-agent/runtime/*"),
        ("model", "revision", "main"),
        ("model", "s3_key", "../Qwen3-8B-Q4_K_M.gguf"),
        ("model", "s3_version_id", ""),
    ],
)
def test_local_inference_lock_rejects_mutable_or_unsafe_artifacts(
    component: str, field: str, value: object
) -> None:
    lock_schema = json.loads(ARTIFACT_LOCK_SCHEMA_PATH.read_text(encoding="utf-8"))
    lock = json.loads(ARTIFACT_LOCK_PATH.read_text(encoding="utf-8"))
    lock["desired"][component][field] = value

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(lock, lock_schema)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("matrix", "e2ee_mode"), "optional"),
        (("gateway", "api_server_enabled"), True),
        (("inference", "base_url"), "https://inference.example.com/v1"),
        (("inference", "base_url"), "http://localhost:8080/v1"),
        (("inference", "fallbacks"), ["openrouter"]),
        (("sandbox", "backend"), "local"),
        (("sandbox", "network"), True),
        (("sandbox", "host_mounts"), True),
        (("sandbox", "credential_passthrough"), True),
        (("sandbox", "allowed_toolsets"), ["hermes-matrix"]),
        (("storage", "encrypted"), False),
        (("operations", "administration"), "ssh"),
        (("operations", "public_ssh"), True),
        (("operations", "public_agent_ingress"), True),
    ],
)
def test_schema_rejects_boundary_widening(path: tuple[str, str], value: object) -> None:
    config = copy.deepcopy(_valid_config())
    config[path[0]][path[1]] = value

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(config, _schema())


def test_schema_requires_nonempty_matrix_allowlists() -> None:
    for key in ("allowed_users", "allowed_rooms"):
        config = copy.deepcopy(_valid_config())
        config["matrix"][key] = []

        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(config, _schema())


def test_schema_requires_hermes_native_profile_matrix_store() -> None:
    config = copy.deepcopy(_valid_config())
    config["matrix"]["crypto_store"] = "/var/lib/hermes/profiles/cloudproof/matrix-crypto"

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(config, _schema())


def test_issue_spec_separates_private_admin_control_from_agent_ingress() -> None:
    spec = ISSUE_SPEC_PATH.read_text(encoding="utf-8")

    required = (
        "Agent Admin Service",
        "internal gRPC",
        "authenticated Gateway admin HTTP edge",
        "desired and observed state",
        "AWS Systems Manager",
        "does not accept prompts or arbitrary tool calls",
        "generic Hermes HTTP/API control surface remains disabled",
    )

    for phrase in required:
        assert phrase in spec


def test_issue_spec_requires_a_dedicated_matrix_user_device_without_appservice_authority() -> None:
    spec = ISSUE_SPEC_PATH.read_text(encoding="utf-8")

    assert "dedicated normal Matrix account and stable device" in spec
    assert "per-profile credential namespace" in spec
    assert "Matrix user/device access token" in spec
    assert "not an application-service or namespace-impersonation token" in spec
    assert "raw credential values" in spec


def test_local_inference_runbook_preserves_bounded_operating_and_rollback_evidence() -> None:
    assert INFERENCE_RUNBOOK_PATH.is_file()
    runbook = INFERENCE_RUNBOOK_PATH.read_text(encoding="utf-8")

    start = runbook.index("## Start from the exact desired generation")
    identity = runbook.index("## Verify bounded lock, READY, and served identities")
    listener = runbook.index("## Prove loopback-only listening")
    routing = runbook.index("## Validate exclusive Hermes routing before Matrix credentials")
    g41 = runbook.index("## G4.1 no-swap fit and Hermes-compatible tool-call proof")
    negative = runbook.index("## Wrong-byte and wrong-config rejection")
    rollback = runbook.index("## Failed desired upgrade and declared rollback")
    disposition = runbook.index("## Evidence disposition")

    start_section = runbook[start:identity]
    assert start_section.index("systemctl restart hermes-inference-prepare.service") < start_section.index(
        '.active_role == "desired"'
    ) < start_section.index("systemctl start hermes-inference.service")

    listener_section = runbook[listener:routing]
    listener_block = listener_section.split("```bash\n", 1)[1].split("\n```", 1)[0]
    assert listener_block == r'''main_pid=$(sudo systemctl show hermes-inference.service -p MainPID --value)
unit_cgroup=$(sudo systemctl show hermes-inference.service -p ControlGroup --value)
test "$main_pid" -gt 1
test -n "$unit_cgroup"
listener=$(sudo ss -H -ltnp 'sport = :8080')
test "$(printf '%s\n' "$listener" | grep -c .)" -eq 1
printf '%s\n' "$listener" | grep -F '127.0.0.1:8080 ' >/dev/null
listener_pid=$(printf '%s\n' "$listener" | sed -nE 's/.*pid=([0-9]+).*/\1/p')
test "$listener_pid" -gt 1
listener_ppid=$(sudo ps -o ppid= -p "$listener_pid" | tr -d '[:space:]')
listener_cgroup=$(sudo sed -n '/^0::/p' "/proc/$listener_pid/cgroup")
test "$listener_ppid" = "$main_pid"
test "$listener_cgroup" = "0::$unit_cgroup"
unset listener listener_pid listener_ppid listener_cgroup main_pid unit_cgroup'''

    g41_section = runbook[g41:negative]
    g41_block = g41_section.split("```bash\n", 1)[1].split("\n```", 1)[0]
    assert g41_block == r'''imds_token=$(curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
  --request PUT \
  --header 'X-aws-ec2-metadata-token-ttl-seconds: 60' \
  http://169.254.169.254/latest/api/token)
instance_type=$(curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
  --header "X-aws-ec2-metadata-token: $imds_token" \
  http://169.254.169.254/latest/meta-data/instance-type)
instance_id=$(curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
  --header "X-aws-ec2-metadata-token: $imds_token" \
  http://169.254.169.254/latest/meta-data/instance-id)
bound_instance_id=$(sudo jq -er '.instance_id' /var/lib/hermes/.active-instance)
test "$instance_type" = 'm7i.2xlarge'
test "$instance_id" = "$bound_instance_id"
unset imds_token instance_type instance_id bound_instance_id
sudo systemctl reset-failed hermes-inference.service
sudo systemctl restart hermes-inference.service
sudo systemctl is-active hermes-inference.service
swapon --show --noheadings
sudo systemctl show hermes-inference.service \
  -p MemoryCurrent -p MemoryPeak -p MemoryMax -p TasksCurrent -p TasksMax'''
    assert (
        "Before entering the node, the operator must also confirm that the SSM target instance ID "
        "equals the reviewed `hermes_cloud_agent_instance_id` Terraform output. The on-node IMDSv2 "
        "check then binds the measured type to the same instance recorded in `.active-instance`; "
        "publish only the two equality booleans, never the raw instance ID or metadata token."
    ) in g41_section
    assert "Run this section only on the declared `m7i.2xlarge`." in g41_section

    negative_section = runbook[negative:rollback]
    negative_block = negative_section.split("```bash\n", 1)[1].split("\n```", 1)[0]
    assert negative_block == r'''uv run --frozen --offline --extra dev -- pytest -q \
  tests/test_hermes_cloud_agent_inference_prepare.py \
  tests/test_hermes_cloud_agent_inference_service.py \
  tests/test_hermes_cloud_agent_local_routing.py \
  tests/test_hermes_cloud_agent_contract.py \
  tests/test_hermes_cloud_agent_iac.py
python3 scripts/verify_hermes_cloud_agent_pinned_routing.py --hermes-source /path/to/clean/pinned/hermes'''
    assert "--with pytest" not in negative_section

    rollback_section = runbook[rollback:disposition]
    assert (
        "The current artifact lock has no declared rollback generation. It also has no safe in-place "
        "lock rollout mechanism: the lock is embedded in EC2 user data, "
        "`user_data_replace_on_change = true` replaces the instance, and the persistent state binding "
        "correctly rejects that replacement identity. Therefore live failed-upgrade rollback evidence "
        "is `BLOCKED`, and C4.5 remains incomplete until separately reviewed work provides both a "
        "previously accepted complete rollback generation and a bounded deployment/recovery transition."
    ) in rollback_section
    assert (
        "The required lock rollout mechanism must stop the gateway and inference units, schema-validate "
        "and atomically install one reviewed root-owned lock, bind the lock to an approved repository "
        "revision and digest, reject caller-selected commands/paths/URLs or partial generations, preserve "
        "the existing instance/volume/device binding, and record only bounded revision/digest/status "
        "evidence. It must also emit a closed, non-sensitive pre-secret failure reason such as "
        "`routing_ready_generation_mismatch` from the same gateway startup path. If replacement is "
        "intended instead, it requires the separately reviewed state-binding recovery transition; clearing "
        "or rewriting `.active-instance` is forbidden. Neither mechanism nor the causal failure attestation "
        "exists in the current candidate, so the exercise below is not executable yet."
    ) in rollback_section
    assert (
        "7. do not attempt the real gateway start as accepted rollback evidence until the startup path "
        "emits and the evidence probe validates the closed `routing_ready_generation_mismatch` reason "
        "before Matrix secret retrieval. A generic failed unit is not causal evidence: `Result` and "
        "`ExecMainStatus` alone cannot distinguish routing rejection from an unrelated startup failure. "
        "This missing bounded attestation keeps the exercise `BLOCKED`."
    ) in rollback_section
    rollback_block = rollback_section.split("```bash\n", 1)[1].split("\n   ```", 1)[0]
    assert rollback_block == r'''   profile_home=$(sudo jq -er '.profile.home' /etc/hermes-cloud-agent/profile.json)
   pinned_model=$(sudo jq -er '.inference.model_id' /etc/hermes-cloud-agent/profile.json)
   pinned_url=$(sudo jq -er '.inference.base_url' /etc/hermes-cloud-agent/profile.json)
   set +e
   validation_output=$(sudo runuser -u hermes -g hermes -G hermes-inference -- env -i \
     HOME="$profile_home" HERMES_HOME="$profile_home" \
     HERMES_STRICT_LOCAL_MODEL_ROUTING=1 \
     HERMES_PINNED_MODEL="$pinned_model" HERMES_PINNED_BASE_URL="$pinned_url" \
     /opt/hermes/venv/bin/python /usr/local/libexec/hermes-validate-local-routing 2>&1)
   validation_status=$?
   set -e
   test "$validation_status" -eq 1
   test "$validation_output" = \
     'local routing validation failed: local routing READY generation mismatch'
   unset validation_output validation_status profile_home pinned_model pinned_url'''
    assert "systemctl start hermes-cloud-agent.service" not in rollback_section

    assert "No raw prompts, responses, tokens, environment dumps, or journal output" in runbook
    assert "Do not edit" in runbook


def test_local_inference_negative_fixture_environment_is_project_locked() -> None:
    pyproject = PYPROJECT_PATH.read_text(encoding="utf-8")
    uv_lock = tomllib.loads(UV_LOCK_PATH.read_text(encoding="utf-8"))

    assert '"pytest==9.1.1"' in pyproject
    packages = {package["name"]: package for package in uv_lock["package"]}
    assert packages["pytest"]["version"] == "9.1.1"
    project = packages["agent-platform"]
    assert {dependency["name"] for dependency in project["optional-dependencies"]["dev"]} == {
        "grpcio-tools",
        "pytest",
        "ruff",
    }
    pytest_requirements = [
        requirement
        for requirement in project["metadata"]["requires-dist"]
        if requirement["name"] == "pytest"
    ]
    assert pytest_requirements == [
        {"name": "pytest", "marker": "extra == 'dev'", "specifier": "==9.1.1"}
    ]


def test_issue_spec_and_changelog_reconcile_c44_without_overclaiming_c45() -> None:
    spec = ISSUE_SPEC_PATH.read_text(encoding="utf-8")
    changelog = CHANGELOG_PATH.read_text(encoding="utf-8")

    status_start = spec.index("## Task 4 delivery status")
    status_end = spec.index("## Tasks — commit boundaries")
    status = spec[status_start:status_end]
    for phrase in (
        "C4.4 complete",
        "ae110cabe8859e782851070d2e16a32b6043eb79",
        "574 tests and 20 subtests",
        "C4.5 remains blocked",
        "G4.1",
        "local-inference-operator-runbook.md",
        "https://github.com/ZenithResearch/hub/actions/runs/30403655158",
    ):
        assert phrase in status
    assert "C4.5 complete" not in spec
    assert "Task 4 complete" not in spec
    assert "local-inference operator runbook" in changelog
