#!/usr/bin/env python3
"""Deploy a reviewed Hypha administration broker image to the existing Synapse host."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Sequence

import verify_fresh_synapse_backup as backup

EXPECTED_PROFILE = "zenith-hypha-synapse"
EXPECTED_ACCOUNT = "610992396917"
EXPECTED_REGION = "us-east-1"
EXPECTED_ROLE_ARN_FRAGMENT = "assumed-role/HyphaSynapseDeploymentRole/"
SECRET_NAME = "hypha/fresh-synapse/runtime"
EXPECTED_REGISTRY = "610992396917.dkr.ecr.us-east-1.amazonaws.com"
EXPECTED_REPOSITORY = "hypha-admin-broker"
IMAGE_PATTERN = re.compile(
    rf"{re.escape(EXPECTED_REGISTRY)}/{EXPECTED_REPOSITORY}@sha256:[0-9a-f]{{64}}"
)
INSTANCE_PATTERN = re.compile(r"i-[0-9a-f]{8,17}")
HOSTNAME_PATTERN = re.compile(
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"
)


class BrokerDeploymentError(RuntimeError):
    """A fail-closed deployment error containing no credential material."""


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_ENDPOINT_URL",
        "AWS_ENDPOINT_URL_STS",
        "AWS_ENDPOINT_URL_SSM",
    ):
        environment.pop(name, None)
    environment["AWS_PROFILE"] = EXPECTED_PROFILE
    environment["AWS_REGION"] = EXPECTED_REGION
    environment["AWS_DEFAULT_REGION"] = EXPECTED_REGION
    return environment


def _run_aws(arguments: Sequence[str]) -> str:
    try:
        process = subprocess.run(
            [
                "aws",
                "--no-cli-pager",
                "--profile",
                EXPECTED_PROFILE,
                "--region",
                EXPECTED_REGION,
                *arguments,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_environment(),
        )
    except OSError as exc:
        raise BrokerDeploymentError("AWS CLI execution failed") from exc
    if process.returncode != 0:
        raise BrokerDeploymentError("AWS CLI command failed")
    return process.stdout


def _json_object(raw: str, description: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BrokerDeploymentError("AWS returned invalid " + description) from exc
    if not isinstance(value, dict):
        raise BrokerDeploymentError("AWS returned invalid " + description)
    return value


def _verify_role() -> None:
    identity = _json_object(
        _run_aws(("sts", "get-caller-identity", "--output", "json")),
        "caller identity",
    )
    arn = identity.get("Arn")
    if (
        identity.get("Account") != EXPECTED_ACCOUNT
        or not isinstance(arn, str)
        or EXPECTED_ROLE_ARN_FRAGMENT not in arn
    ):
        raise BrokerDeploymentError("bounded deployment identity was not established")


def _encode_script(source: str) -> str:
    return base64.b64encode(source.encode()).decode("ascii")


def _configuration_rewriter(*, hostname: str, image: str) -> str:
    broker_block = f"""  # BEGIN HYPHA ADMIN BROKER
  hypha-admin-broker:
    image: {image}
    container_name: hypha-admin-broker
    restart: unless-stopped
    depends_on: [matrix-synapse]
    user: "65532:65532"
    read_only: true
    env_file: /opt/matrix/broker.env
    environment:
      HYPHA_ADMIN_BROKER_SERVICE_USER_ID: '@_hypha_admin_broker:{hostname}'
    tmpfs:
      - /tmp:noexec,nosuid,size=16m
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    networks: [matrix-internal]
  # END HYPHA ADMIN BROKER

"""
    old_caddy = f"""{hostname} {{
  encode zstd gzip
  reverse_proxy matrix-synapse:8008
}}
"""
    previous_caddy = f"""{hostname} {{
  encode zstd gzip
  handle /_hypha/admin/v1/* {{
    reverse_proxy hypha-admin-broker:8080
  }}
  handle {{
    reverse_proxy matrix-synapse:8008
  }}
}}
"""
    new_caddy = f"""{hostname} {{
  encode zstd gzip
  handle /_hypha/admin/v1/* {{
    request_body {{
      max_size 64KB
    }}
    reverse_proxy hypha-admin-broker:8080
  }}
  handle {{
    reverse_proxy matrix-synapse:8008
  }}
}}
"""
    return f"""import os
from pathlib import Path

compose_path = Path("/opt/matrix/compose.yaml")
caddy_path = Path("/opt/matrix/Caddyfile")
compose = compose_path.read_text(encoding="utf-8")
caddy = caddy_path.read_text(encoding="utf-8")
begin = "  # BEGIN HYPHA ADMIN BROKER\\n"
end = "  # END HYPHA ADMIN BROKER\\n"
broker_block = {broker_block!r}
if begin in compose or end in compose:
    if compose.count(begin) != 1 or compose.count(end) != 1:
        raise SystemExit("managed broker compose block is invalid")
    prefix, remainder = compose.split(begin, 1)
    _, suffix = remainder.split(end, 1)
    compose = prefix + broker_block + suffix.lstrip("\\n")
else:
    if "  hypha-admin-broker:" in compose or compose.count("  caddy:\\n") != 1:
        raise SystemExit("unmanaged broker compose configuration detected")
    compose = compose.replace("  caddy:\\n", broker_block + "  caddy:\\n")
old_caddy = {old_caddy!r}
previous_caddy = {previous_caddy!r}
new_caddy = {new_caddy!r}
if caddy not in {{old_caddy, previous_caddy, new_caddy}}:
    raise SystemExit("unmanaged Caddy configuration detected")
for path, content in ((compose_path, compose), (caddy_path, new_caddy)):
    temporary = path.with_suffix(path.suffix + ".hypha-new")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o600 if path.name == "compose.yaml" else 0o644)
    os.replace(temporary, path)
"""


def _secret_renderer() -> str:
    return r"""import json
import os
from pathlib import Path
import re
import sys

secret_path, broker_path, bootstrap_path, server_name = sys.argv[1:]
required = {
    "POSTGRES_PASSWORD",
    "REGISTRATION_SHARED_SECRET",
    "MACAROON_SECRET_KEY",
    "FORM_SECRET",
    "HYPHA_ADMIN_BROKER_SECRET_VERIFIER",
    "HYPHA_ADMIN_BROKER_SERVICE_PASSWORD",
}
with open(secret_path, encoding="utf-8") as handle:
    values = json.load(handle)
if not isinstance(values, dict) or set(values) != required:
    raise SystemExit("runtime secret schema is invalid")
ordinary = required - {"HYPHA_ADMIN_BROKER_SECRET_VERIFIER"}
if any(not isinstance(values[key], str) or not re.fullmatch(r"[A-Za-z0-9._~!@#%^*+=:-]{32,512}", values[key]) for key in ordinary):
    raise SystemExit("runtime secret value format is invalid")
if not isinstance(values["HYPHA_ADMIN_BROKER_SECRET_VERIFIER"], str) or not re.fullmatch(
    r"scrypt\$[0-9]+\$[0-9]+\$[0-9]+\$[A-Za-z0-9_-]+\$[A-Za-z0-9_-]+",
    values["HYPHA_ADMIN_BROKER_SECRET_VERIFIER"],
):
    raise SystemExit("runtime broker verifier format is invalid")
broker = Path(broker_path)
verifier_key = "HYPHA_ADMIN_BROKER_SECRET_VERIFIER"
service_password_key = "HYPHA_ADMIN_BROKER_SERVICE_PASSWORD"
broker.write_text(
    verifier_key + "='" + values[verifier_key] + "'\n"
    + service_password_key + "='" + values[service_password_key] + "'\n",
    encoding="utf-8",
)
bootstrap = Path(bootstrap_path)
bootstrap.write_text(
    "REGISTRATION_SHARED_SECRET='" + values["REGISTRATION_SHARED_SECRET"] + "'\n"
    + service_password_key + "='" + values[service_password_key] + "'\n"
    + "MATRIX_SERVER_NAME='" + server_name + "'\n",
    encoding="utf-8",
)
os.chmod(broker, 0o600)
os.chmod(bootstrap, 0o600)
"""


def deployment_commands(*, hostname: str, image: str) -> tuple[str, ...]:
    rewrite = _encode_script(_configuration_rewriter(hostname=hostname, image=image))
    render_secret = _encode_script(_secret_renderer())
    expected_digest = image.rsplit("@sha256:", 1)[1]
    return (
        "set -euo pipefail",
        "umask 077",
        "MATRIX_DIR=/opt/matrix",
        'backup="$MATRIX_DIR/backups/hypha-admin-broker-$(date -u +%Y%m%dT%H%M%SZ)"',
        'mkdir -p "$backup"',
        'cp --preserve=mode,ownership "$MATRIX_DIR/compose.yaml" "$backup/compose.yaml"',
        'cp --preserve=mode,ownership "$MATRIX_DIR/Caddyfile" "$backup/Caddyfile"',
        'if [ -f "$MATRIX_DIR/broker.env" ]; then cp --preserve=mode,ownership "$MATRIX_DIR/broker.env" "$backup/broker.env"; fi',
        'rollback() { set +e; docker compose --project-directory "$MATRIX_DIR" -f "$MATRIX_DIR/compose.yaml" stop hypha-admin-broker; cp "$backup/compose.yaml" "$MATRIX_DIR/compose.yaml"; cp "$backup/Caddyfile" "$MATRIX_DIR/Caddyfile"; if [ -f "$backup/broker.env" ]; then cp "$backup/broker.env" "$MATRIX_DIR/broker.env"; else rm -f "$MATRIX_DIR/broker.env"; fi; docker compose --project-directory "$MATRIX_DIR" -f "$MATRIX_DIR/compose.yaml" up -d --remove-orphans; }',
        "trap rollback ERR",
        "secret_json=$(mktemp /run/hypha-admin-broker-secret.XXXXXX)",
        "bootstrap_env=$(mktemp /run/hypha-admin-broker-bootstrap.XXXXXX)",
        "docker_config=$(mktemp -d /run/hypha-admin-broker-docker.XXXXXX)",
        'export DOCKER_CONFIG="$docker_config"',
        'trap \'rm -f "$secret_json" "$bootstrap_env"; rm -rf "$docker_config"\' EXIT',
        f"printf '%s' '{rewrite}' | base64 -d | python3 -",
        f"aws ecr get-login-password --region '{EXPECTED_REGION}' | docker login --username AWS --password-stdin '{EXPECTED_REGISTRY}' >/dev/null",
        f"docker pull '{image}' >/dev/null",
        f"docker image inspect '{image}' --format '{{{{json .RepoDigests}}}}' | grep -F 'sha256:{expected_digest}' >/dev/null",
        f"aws secretsmanager get-secret-value --region '{EXPECTED_REGION}' --secret-id '{SECRET_NAME}' --version-stage AWSCURRENT --query SecretString --output text > \"$secret_json\"",
        f"printf '%s' '{render_secret}' | base64 -d | python3 - \"$secret_json\" \"$MATRIX_DIR/broker.env\" \"$bootstrap_env\" '{hostname}'",
        'docker compose --project-directory "$MATRIX_DIR" -f "$MATRIX_DIR/compose.yaml" config --quiet',
        f"docker run --rm --network matrix_matrix-internal --env-file \"$bootstrap_env\" --entrypoint python '{image}' /app/scripts/bootstrap_hypha_admin_broker_authority.py >/dev/null",
        'rm -f "$secret_json" "$bootstrap_env"',
        'docker compose --project-directory "$MATRIX_DIR" -f "$MATRIX_DIR/compose.yaml" up -d hypha-admin-broker matrix-caddy',
        'for attempt in $(seq 1 60); do status=$(docker inspect --format=\'{{.State.Health.Status}}\' hypha-admin-broker 2>/dev/null || true); [ "$status" = healthy ] && break; [ "$attempt" -lt 60 ] || { echo "broker did not become healthy" >&2; exit 1; }; sleep 5; done',
        'test "$(docker inspect --format=\'{{.Config.User}}\' hypha-admin-broker)" = "65532:65532"',
        'test "$(docker inspect --format=\'{{json .HostConfig.PortBindings}}\' hypha-admin-broker)" = "null"',
        "docker exec hypha-admin-broker python -c \"import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8080/_hypha/admin/v1/health', timeout=5); assert r.status == 200\"",
        "docker exec hypha-admin-broker python -c \"import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8080/_hypha/admin/v1/ready', timeout=15); assert r.status == 200\"",
        f"test \"$(curl --silent --show-error --max-time 15 --output /dev/null --write-out '%{{http_code}}' 'https://{hostname}/_hypha/admin/v1/ready')\" = \"200\"",
        f"test \"$(curl --silent --show-error --max-time 15 --output /dev/null --write-out '%{{http_code}}' 'https://{hostname}/_matrix/client/versions')\" = \"200\"",
        "trap - ERR",
        'printf "%s\\n" "Hypha administration broker deployment verified"',
    )


def _send_command(instance_id: str, commands: Sequence[str]) -> str:
    with TemporaryDirectory(prefix="hypha-admin-broker-deploy-") as temporary_name:
        parameters = Path(temporary_name) / "parameters.json"
        parameters.write_text(json.dumps({"commands": list(commands)}), encoding="utf-8")
        os.chmod(parameters, 0o600)
        raw = _run_aws(
            (
                "ssm",
                "send-command",
                "--instance-ids",
                instance_id,
                "--document-name",
                "AWS-RunShellScript",
                "--comment",
                "Deploy reviewed Hypha administration broker",
                "--parameters",
                "file://" + str(parameters),
                "--output",
                "json",
            )
        )
    result = _json_object(raw, "deployment command metadata")
    command = result.get("Command")
    command_id = command.get("CommandId") if isinstance(command, dict) else None
    if not isinstance(command_id, str) or not command_id:
        raise BrokerDeploymentError("AWS returned invalid deployment command metadata")
    return command_id


def _wait_for_command(command_id: str) -> None:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        raw = _run_aws(("ssm", "list-commands", "--command-id", command_id, "--output", "json"))
        result = _json_object(raw, "deployment command status")
        commands = result.get("Commands")
        status = commands[0].get("Status") if isinstance(commands, list) and commands else None
        if status == "Success":
            return
        if status in {"Cancelled", "Cancelling", "Failed", "TimedOut"}:
            raise BrokerDeploymentError("broker deployment failed and invoked rollback")
        time.sleep(5)
    raise BrokerDeploymentError("broker deployment timed out")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):  # type: ignore[no-untyped-def]
        return None


def _verify_public_endpoint(url: str, expected_status: int) -> None:
    request = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:  # noqa: S310
            body = response.read(64 * 1024 + 1)
            status = response.status
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        body = exc.read(64 * 1024 + 1)
        status = exc.code
        final_url = exc.geturl()
    except (OSError, TimeoutError) as exc:
        raise BrokerDeploymentError("public deployment verification failed") from exc
    if status != expected_status or final_url != url or len(body) > 64 * 1024:
        raise BrokerDeploymentError("public deployment verification failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--admin-broker-image", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.profile != EXPECTED_PROFILE or args.region != EXPECTED_REGION:
        print("refusing deployment: exact profile and region are required", file=sys.stderr)
        return 2
    if not INSTANCE_PATTERN.fullmatch(args.instance_id):
        print("refusing deployment: an explicit EC2 instance ID is required", file=sys.stderr)
        return 2
    if not HOSTNAME_PATTERN.fullmatch(args.hostname):
        print("refusing deployment: hostname must be a lowercase FQDN", file=sys.stderr)
        return 2
    if not IMAGE_PATTERN.fullmatch(args.admin_broker_image):
        print("refusing deployment: an immutable broker image digest is required", file=sys.stderr)
        return 2
    commands = deployment_commands(hostname=args.hostname, image=args.admin_broker_image)
    if args.dry_run:
        digest = hashlib.sha256(json.dumps(commands).encode()).hexdigest()
        print(
            json.dumps(
                {"command_bundle_sha256": digest, "instance_id": args.instance_id}, sort_keys=True
            )
        )
        return 0
    try:
        _verify_role()
        backup.verify_backup(args.instance_id, run_aws=_run_aws)
        command_id = _send_command(args.instance_id, commands)
        _wait_for_command(command_id)
        origin = "https://" + args.hostname
        _verify_public_endpoint(origin + "/_hypha/admin/v1/health", 200)
        _verify_public_endpoint(origin + "/_hypha/admin/v1/ready", 200)
        _verify_public_endpoint(origin + "/_matrix/client/versions", 200)
    except (backup.BackupVerificationError, BrokerDeploymentError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "admin_broker_image": args.admin_broker_image,
                "instance_id": args.instance_id,
                "status": "deployed_and_verified",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
