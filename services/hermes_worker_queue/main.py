"""
Hermes Worker Queue Consumer

Consumes durable worker assignments from the shared worker queue, launches
Hermes with the resolved dispatch profile, and only then acknowledges the
assignment queue message.

Required environment variables:
  QUEUE_HTTP_URL                 http://queue:8081
  CASES_HTTP_URL                 http://cases:8083
  EVENTBUS_URL                   http://eventbus:8082
  HERMES_HOME                    root Hermes home, typically /hub/.hermes

Optional:
  WORKER_QUEUE_NAME              default: workers
  WORKER_ID                      default: hermes-worker
  RECONNECT_DELAY_S              default: 5
  HERMES_FORWARD_CASES_HTTP_URL  default: CASES_HTTP_URL
  GATEWAY_HTTP_URL               default: http://gateway-http:8080
  TERMINAL_CWD                   default: /hub
  LOG_LEVEL                      default: info
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sys
import textwrap
import subprocess
from pathlib import Path
from typing import Any

import httpx
import yaml

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "info").upper(),
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
    force=True,
)
log = logging.getLogger("hermes_worker_queue")

QUEUE_URL = os.environ["QUEUE_HTTP_URL"].rstrip("/")
CASES_URL = os.environ["CASES_HTTP_URL"].rstrip("/")
EVENTBUS_URL = os.environ["EVENTBUS_URL"].rstrip("/")
WORKER_QUEUE_NAME = os.environ.get("WORKER_QUEUE_NAME", "workers")
WORKER_ID = os.environ.get("WORKER_ID", "hermes-worker")
TOPIC = "queue.job.enqueued"
RECONNECT_DELAY = float(os.environ.get("RECONNECT_DELAY_S", "5"))
HERMES_HOME_ROOT = Path(os.environ.get("HERMES_HOME", "/hub/.hermes")).resolve()
HERMES_FORWARD_CASES_URL = os.environ.get("HERMES_FORWARD_CASES_HTTP_URL", CASES_URL).strip()
GATEWAY_HTTP_URL = os.environ.get("GATEWAY_HTTP_URL", "http://gateway-http:8080").strip()
TERMINAL_CWD = Path(os.environ.get("TERMINAL_CWD", "/hub")).resolve()
WORKER_LOG_DIR = HERMES_HOME_ROOT / "logs" / "worker_queue"
DEFAULT_PROFILE_NAME = "default"
PRELOADED_SKILL_NAMES = ("case-execution-loop", "step-execution-loop")
CANONICAL_SKILLS_DIR = Path(
    os.environ.get("HERMES_CANONICAL_SKILLS_DIR", "/hub/rolodex/agents/frank/skills/worker")
).resolve()
HERMES_PROFILES_DIR = HERMES_HOME_ROOT / "profiles"
DISPATCH_RUNTIME_ROOT = HERMES_HOME_ROOT / "dispatch_runtime"
DISPATCH_MODEL_PROVIDER = os.environ.get("HERMES_DISPATCH_PROVIDER", "openai-codex").strip() or "openai-codex"
DISPATCH_MODEL_NAME = (
    os.environ.get("HERMES_DISPATCH_MODEL", "gpt-5.3-codex").strip()
    or "gpt-5.3-codex"
)
DISPATCH_AUX_PROVIDER = os.environ.get("HERMES_DISPATCH_AUX_PROVIDER", "main").strip() or "main"
_RAW_DISPATCH_AUX_MODEL = os.environ.get("HERMES_DISPATCH_AUX_MODEL")
if _RAW_DISPATCH_AUX_MODEL is None and DISPATCH_AUX_PROVIDER in {"main", "auto"}:
    DISPATCH_AUX_MODEL_NAME = ""
else:
    DISPATCH_AUX_MODEL_NAME = (_RAW_DISPATCH_AUX_MODEL or DISPATCH_MODEL_NAME).strip()
    if not DISPATCH_AUX_MODEL_NAME and DISPATCH_AUX_PROVIDER not in {"main", "auto"}:
        DISPATCH_AUX_MODEL_NAME = DISPATCH_MODEL_NAME
BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _is_stale_local_codex_bridge_url(value: Any) -> bool:
    """Return true only for legacy local Codex bridge endpoints on port 3690."""
    if not isinstance(value, str):
        return False
    normalized = value.strip().rstrip("/").lower()
    if not normalized:
        return False
    host_docker_internal = "host." + "docker.internal"
    local_hosts = (
        f"http://{host_docker_internal}:3690",
        "http://localhost:3690",
        "http://127.0.0.1:3690",
        "http://0.0.0.0:3690",
    )
    return any(normalized == host or normalized == f"{host}/v1" for host in local_hosts)


def _apply_dispatch_model_config(slot: dict[str, Any], *, provider: str, model: str) -> dict[str, Any]:
    """Apply a named Hermes provider without leaking stale Codex bridge endpoints."""
    updated = dict(slot)
    normalized_provider = provider.strip().lower()
    updated["provider"] = provider
    updated["model"] = model

    if normalized_provider == "openai-codex":
        updated.pop("base_url", None)
        updated.pop("api_key", None)
    elif normalized_provider == "openrouter":
        if _is_stale_local_codex_bridge_url(updated.get("base_url")):
            updated.pop("base_url", None)
    elif normalized_provider in {"custom", "openai"}:
        pass
    else:
        updated.pop("base_url", None)
        updated.pop("api_key", None)

    return updated


async def dequeue(client: httpx.AsyncClient) -> dict[str, Any] | None:
    response = await client.post(
        f"{QUEUE_URL}/queues/{WORKER_QUEUE_NAME}/dequeue",
        params={"worker_id": WORKER_ID},
        timeout=10.0,
    )
    response.raise_for_status()
    data = response.json()
    return data["message"] if data.get("found") else None


async def ack(client: httpx.AsyncClient, message_id: str, result: dict[str, Any] | None = None) -> None:
    response = await client.post(
        f"{QUEUE_URL}/messages/{message_id}/ack",
        json={"result": result or {}},
        timeout=5.0,
    )
    response.raise_for_status()


async def nack(client: httpx.AsyncClient, message_id: str, reason: str) -> None:
    response = await client.post(
        f"{QUEUE_URL}/messages/{message_id}/nack",
        json={"reason": reason},
        timeout=5.0,
    )
    response.raise_for_status()


async def get_case_detail(client: httpx.AsyncClient, case_id: str) -> dict[str, Any]:
    response = await client.get(f"{CASES_URL}/cases/{case_id}", timeout=10.0)
    response.raise_for_status()
    return response.json()


async def update_case_status(client: httpx.AsyncClient, case_id: str, status: str) -> None:
    response = await client.put(
        f"{CASES_URL}/cases/{case_id}/status",
        json={"status": status},
        timeout=10.0,
    )
    response.raise_for_status()


async def append_case_log(
    client: httpx.AsyncClient,
    case_id: str,
    log_type: str,
    message: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    response = await client.post(
        f"{CASES_URL}/cases/{case_id}/logs",
        json={"type": log_type, "message": message, "metadata": metadata or {}},
        timeout=10.0,
    )
    response.raise_for_status()


async def append_case_log_safe(
    client: httpx.AsyncClient,
    case_id: str,
    log_type: str,
    message: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        await append_case_log(client, case_id, log_type, message, metadata=metadata)
    except Exception as exc:
        log.warning("Failed to append case log  case_id=%s  error=%s", case_id, exc)


def managed_skills_dir_for_home(hermes_home: Path) -> Path:
    return hermes_home / "skills" / "worker"


def case_execution_skill_dir(hermes_home: Path) -> Path:
    return managed_skills_dir_for_home(hermes_home) / "case-execution-loop"


def asset_fetch_helper_path(hermes_home: Path) -> Path:
    return case_execution_skill_dir(hermes_home) / "scripts" / "fetch_review_assets.py"


def worker_cli_path(hermes_home: Path) -> Path:
    return case_execution_skill_dir(hermes_home) / "scripts" / "worker_cli.py"


def profile_home(profile: str) -> Path:
    return HERMES_PROFILES_DIR / profile


def active_hermes_home(profile: str | None) -> Path:
    return profile_home(profile).resolve() if profile else HERMES_HOME_ROOT


def dispatch_runtime_home(profile: str | None) -> Path:
    runtime_name = (profile or DEFAULT_PROFILE_NAME).strip() or DEFAULT_PROFILE_NAME
    return (DISPATCH_RUNTIME_ROOT / runtime_name).resolve()


def _skill_target_homes(profile: str | None = None) -> list[Path]:
    targets: list[Path] = [HERMES_HOME_ROOT]
    if HERMES_PROFILES_DIR.exists():
        for child in sorted(HERMES_PROFILES_DIR.iterdir()):
            if child.is_dir():
                targets.append(child)
    if profile:
        selected = profile_home(profile)
        if selected not in targets:
            targets.append(selected)
    unique_targets: list[Path] = []
    seen: set[Path] = set()
    for target in targets:
        resolved = target.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_targets.append(resolved)
    return unique_targets


def sync_repo_skills(profile: str | None = None) -> dict[str, list[str]]:
    installed_by_home: dict[str, list[str]] = {}

    for hermes_home in _skill_target_homes(profile):
        managed_skills_dir = managed_skills_dir_for_home(hermes_home)
        managed_skills_dir.mkdir(parents=True, exist_ok=True)
        installed: list[str] = []

        for skill_name in PRELOADED_SKILL_NAMES:
            source_dir = CANONICAL_SKILLS_DIR / skill_name
            source_file = source_dir / "SKILL.md"
            if not source_file.exists():
                log.warning("Canonical skill missing  path=%s", source_file)
                continue
            destination_dir = managed_skills_dir / skill_name
            if destination_dir.exists():
                shutil.rmtree(destination_dir)
            shutil.copytree(source_dir, destination_dir)
            installed.append(skill_name)

        installed_by_home[str(hermes_home)] = installed

    return installed_by_home


def sync_repo_skills_to_homes(homes: list[Path]) -> dict[str, list[str]]:
    installed_by_home: dict[str, list[str]] = {}

    for hermes_home in homes:
        managed_skills_dir = managed_skills_dir_for_home(hermes_home)
        managed_skills_dir.mkdir(parents=True, exist_ok=True)
        installed: list[str] = []

        for skill_name in PRELOADED_SKILL_NAMES:
            source_dir = CANONICAL_SKILLS_DIR / skill_name
            source_file = source_dir / "SKILL.md"
            if not source_file.exists():
                log.warning("Canonical skill missing  path=%s", source_file)
                continue
            destination_dir = managed_skills_dir / skill_name
            if destination_dir.exists():
                shutil.rmtree(destination_dir)
            shutil.copytree(source_dir, destination_dir)
            installed.append(skill_name)

        installed_by_home[str(hermes_home)] = installed

    return installed_by_home


def materialize_dispatch_runtime_home(profile: str | None) -> dict[str, str]:
    source_home = active_hermes_home(profile)
    runtime_home = dispatch_runtime_home(profile)
    if runtime_home.exists():
        shutil.rmtree(runtime_home)
    runtime_home.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source_home,
        runtime_home,
        ignore=shutil.ignore_patterns("logs", "sessions", "dispatch_runtime"),
    )

    config_path = runtime_home / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    config = config or {}
    config["model"] = _apply_dispatch_model_config(
        dict(config.get("model") or {}),
        provider=DISPATCH_MODEL_PROVIDER,
        model=DISPATCH_MODEL_NAME,
    )
    config["auxiliary"] = _apply_dispatch_model_config(
        dict(config.get("auxiliary") or {}),
        provider=DISPATCH_AUX_PROVIDER,
        model=DISPATCH_AUX_MODEL_NAME,
    )
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    return {
        "source_home": str(source_home),
        "runtime_home": str(runtime_home),
        "model_provider": DISPATCH_MODEL_PROVIDER,
        "model_name": DISPATCH_MODEL_NAME,
        "aux_provider": DISPATCH_AUX_PROVIDER,
        "aux_model_name": DISPATCH_AUX_MODEL_NAME,
    }


def validate_preloaded_skills(profile: str | None) -> dict[str, Any]:
    hermes_home = active_hermes_home(profile)
    return validate_preloaded_skills_for_home(hermes_home)


def validate_preloaded_skills_for_home(hermes_home: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    probe = textwrap.dedent(
        f"""
        import json, sys
        from agent.skill_commands import build_preloaded_skills_prompt

        _, loaded, missing = build_preloaded_skills_prompt({list(PRELOADED_SKILL_NAMES)!r})
        print(json.dumps({{"loaded": loaded, "missing": missing}}))
        raise SystemExit(0 if not missing else 2)
        """
    ).strip()
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(TERMINAL_CWD),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    stdout_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    payload: dict[str, Any] = {"loaded": [], "missing": []}
    if stdout_lines:
        try:
            payload = json.loads(stdout_lines[-1])
        except json.JSONDecodeError:
            payload = {"loaded": [], "missing": list(PRELOADED_SKILL_NAMES)}
    if result.returncode != 0:
        raise RuntimeError(
            "Hermes skill preload preflight failed "
            f"for home={hermes_home}: stdout={result.stdout.strip()!r} stderr={result.stderr.strip()!r}"
        )
    return {
        "hermes_home": str(hermes_home),
        "loaded": list(payload.get("loaded") or []),
        "missing": list(payload.get("missing") or []),
    }


def prepare_profile_skills(profile: str | None) -> dict[str, Any]:
    runtime = materialize_dispatch_runtime_home(profile)
    installed_by_home = sync_repo_skills_to_homes([Path(runtime["runtime_home"])])
    validation = validate_preloaded_skills_for_home(Path(runtime["runtime_home"]))
    if validation["missing"]:
        raise RuntimeError(
            f"Preloaded skills missing for home={validation['hermes_home']}: {validation['missing']}"
        )
    return {
        "source_home": runtime["source_home"],
        "runtime_home": runtime["runtime_home"],
        "model_provider": runtime["model_provider"],
        "model_name": runtime["model_name"],
        "aux_provider": runtime["aux_provider"],
        "aux_model_name": runtime["aux_model_name"],
        "skills_home": validation["hermes_home"],
        "skills_loaded": validation["loaded"],
        "skills_synced_homes": sorted(installed_by_home.keys()),
        "asset_fetch_helper_path": str(asset_fetch_helper_path(Path(runtime["runtime_home"]))),
        "worker_cli_path": str(worker_cli_path(Path(runtime["runtime_home"]))),
    }


def build_worker_prompt(case_id: str, dispatch_packet: dict[str, Any], profile: str | None = None) -> str:
    process_summary = dispatch_packet.get("process_summary") or {}
    assignment = dispatch_packet.get("assignment") or {}
    step_briefs = (
        dispatch_packet.get("resolved_step_briefs")
        or dispatch_packet.get("step_briefs")
        or dispatch_packet.get("steps")
        or []
    )
    initial_context = dispatch_packet.get("initial_context") or {}
    worker_instructions = dispatch_packet.get("worker_instructions") or []
    case_execution_rules = (
        dispatch_packet.get("worker_execution_rules")
        or dispatch_packet.get("case_execution_rules")
        or []
    )
    runtime_home = dispatch_runtime_home(profile)
    helper_path = asset_fetch_helper_path(runtime_home)
    cli_path = worker_cli_path(runtime_home)
    review_asset_context = {
        "review_id": initial_context.get("review_id"),
        "events_asset_id": initial_context.get("events_asset_id"),
        "audio_asset_id": initial_context.get("audio_asset_id"),
        "gateway_http_url": GATEWAY_HTTP_URL,
        "asset_fetch_helper_path": str(helper_path),
        "worker_cli_path": str(cli_path),
    }
    prompt_lines = [
        f"You are executing dispatched case {case_id}.",
        "",
        "Treat the cases service as the durable source of truth.",
        "Fetch the case, read dispatch_packet_json, and follow the preloaded case-execution-loop skill faithfully.",
        "",
        "Process summary:",
        json.dumps(process_summary, indent=2, sort_keys=True),
        "",
        "Assignment:",
        json.dumps(
            {
                "assignment_id": assignment.get("assignment_id"),
                "executor": assignment.get("executor"),
                "dispatch_profile": assignment.get("dispatch_profile"),
                "policy": assignment.get("policy"),
            },
            indent=2,
            sort_keys=True,
        ),
        "",
        "Initial context to write into case slots before step execution:",
        json.dumps(initial_context, indent=2, sort_keys=True),
        "",
        "Review asset fetch context:",
        json.dumps(review_asset_context, indent=2, sort_keys=True),
        "",
        "Worker instructions:",
    ]
    prompt_lines.extend(f"- {line}" for line in worker_instructions)
    prompt_lines.extend(["", "Case execution rules:"])
    prompt_lines.extend(f"- {line}" for line in case_execution_rules)
    prompt_lines.extend(
        [
            "",
            "Step briefs:",
            json.dumps(step_briefs, indent=2, sort_keys=True),
            "",
            "If review asset IDs are present, run the asset fetch helper before any step assumes files already exist on disk.",
            "The helper must be used to materialize events and audio locally by asset_id; do not guess under /hub/data/reviews/assets.",
            "Run only the steps that are currently runnable for you according to live case slot state.",
            "Before each spawn wave, re-fetch the case and re-evaluate readiness just in time.",
            "Spawn one subagent per runnable step in parallel and persist step runtime/task state while those steps are active.",
            "Use the preloaded case-execution-loop and step-execution-loop skills as the authoritative operational procedure.",
            "Do not redefine the DAG, do not invent slot names, and do not drift into generic repository exploration.",
            "Use worker_cli.py as the deterministic orchestration surface for loading the case, materializing assets, computing ready waves, updating runtime state, and committing outputs.",
            "Run Step 1 locally in the parent worker, then compute later runnable waves.",
            "Delegated step runners must return structured JSON only; the parent validates and commits wave outputs.",
        ]
    )
    return textwrap.dedent("\n".join(prompt_lines)).strip()


def build_hermes_command(case_id: str, profile: str | None, prompt: str) -> list[str]:
    command = ["hermes"]
    command.extend(["--skills", ",".join(PRELOADED_SKILL_NAMES)])
    command.extend(["chat", "-q", prompt])
    return command


def redact_prompt_argv(command: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for part in command:
        if skip_next:
            redacted.append("[REDACTED_PROMPT]")
            skip_next = False
            continue
        redacted.append(part)
        if part == "-q":
            skip_next = True
    return redacted


def safe_launch_log_metadata(launch_result: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in launch_result.items():
        if key == "prompt_preview":
            continue
        if key == "command" and isinstance(value, list):
            safe[key] = redact_prompt_argv([str(part) for part in value])
            continue
        safe[key] = value
    return safe


def launch_hermes_session(
    case_id: str,
    profile: str | None,
    dispatch_packet: dict[str, Any],
    runtime_home: str,
) -> tuple[subprocess.Popen[bytes], dict[str, Any]]:
    WORKER_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = WORKER_LOG_DIR / f"{case_id}.log"
    prompt = build_worker_prompt(case_id, dispatch_packet, profile)
    command = build_hermes_command(case_id, profile, prompt)

    env = os.environ.copy()
    env["HERMES_HOME"] = runtime_home
    if HERMES_FORWARD_CASES_URL:
        env["CASES_HTTP_URL"] = HERMES_FORWARD_CASES_URL
    if GATEWAY_HTTP_URL:
        env["GATEWAY_HTTP_URL"] = GATEWAY_HTTP_URL

    stdout_handle = open(log_path, "ab")
    process = subprocess.Popen(
        command,
        cwd=str(TERMINAL_CWD),
        env=env,
        stdout=stdout_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    stdout_handle.close()
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return process, {
        "pid": process.pid,
        "command": redact_prompt_argv(command),
        "prompt_length": len(prompt),
        "prompt_sha256": prompt_sha256,
        "log_path": str(log_path),
        "runtime_home": runtime_home,
        "preloaded_skills": list(PRELOADED_SKILL_NAMES),
        "gateway_http_url": GATEWAY_HTTP_URL,
        "asset_fetch_helper_path": str(asset_fetch_helper_path(Path(runtime_home))),
        "worker_cli_path": str(worker_cli_path(Path(runtime_home))),
    }


def list_session_artifacts(runtime_home: Path) -> list[Path]:
    sessions_dir = runtime_home / "sessions"
    if not sessions_dir.exists():
        return []
    return sorted(path.resolve() for path in sessions_dir.glob("session_*.json") if path.is_file())


def read_session_artifact(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "session_id": str(payload.get("session_id") or path.stem),
        "model": payload.get("model"),
        "base_url": payload.get("base_url"),
    }


def _track_background_task(task: asyncio.Task[Any]) -> None:
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)


async def monitor_worker_session(
    case_id: str,
    assignment_id: str,
    selected_profile: str,
    runtime_home: str,
    log_path: str,
    process: subprocess.Popen[bytes],
) -> None:
    runtime_home_path = Path(runtime_home)
    session_logged = False
    session_artifact_path: str | None = None
    session_id: str | None = None
    while True:
        if not session_logged:
            artifacts = list_session_artifacts(runtime_home_path)
            if artifacts:
                latest = artifacts[-1]
                try:
                    artifact_info = read_session_artifact(latest)
                except Exception as exc:
                    artifact_info = {"session_id": latest.stem, "parse_error": str(exc)}
                session_logged = True
                session_artifact_path = str(latest)
                session_id = str(artifact_info.get("session_id") or latest.stem)
                async with httpx.AsyncClient() as client:
                    await append_case_log_safe(
                        client,
                        case_id,
                        "dispatch",
                        "worker session artifact ready",
                        metadata={
                            "assignment_id": assignment_id,
                            "selected_profile": selected_profile,
                            "runtime_home": runtime_home,
                            "log_path": log_path,
                            "session_json_path": session_artifact_path,
                            "session_id": session_id,
                            "session_export_format": "hermes-session-json",
                            "model": artifact_info.get("model"),
                            "base_url": artifact_info.get("base_url"),
                        },
                    )
        returncode = process.poll()
        if returncode is not None:
            async with httpx.AsyncClient() as client:
                await append_case_log_safe(
                    client,
                    case_id,
                    "dispatch",
                    "worker execution exited",
                    metadata={
                        "assignment_id": assignment_id,
                        "selected_profile": selected_profile,
                        "runtime_home": runtime_home,
                        "log_path": log_path,
                        "returncode": returncode,
                        "session_json_path": session_artifact_path,
                        "session_id": session_id,
                    },
                )
            return
        await asyncio.sleep(0.5)


def resolve_selected_profile(dispatch_profile: str | None) -> tuple[str | None, str]:
    profile = (dispatch_profile or "").strip() or None
    return profile, profile or DEFAULT_PROFILE_NAME


def resolve_effective_policy(dispatch_packet: dict[str, Any]) -> dict[str, Any]:
    assignment = dispatch_packet.get("assignment") or {}
    policy = assignment.get("policy") or {}
    return {
        "required_skills": list(policy.get("required_skills") or []),
        "allowed_tools": list(policy.get("allowed_tools") or []),
        "denied_tools": list(policy.get("denied_tools") or []),
        "resource_scopes": list(policy.get("resource_scopes") or []),
    }


async def handle_assignment(client: httpx.AsyncClient) -> None:
    msg = await dequeue(client)
    if msg is None:
        return

    message_id = msg["id"]
    payload = msg.get("payload") or {}
    case_id = str(payload.get("case_id") or "").strip()
    assignment_id = str(payload.get("assignment_id") or "").strip()
    dispatch_profile = payload.get("dispatch_profile")
    executor = payload.get("executor")
    profile, selected_profile = resolve_selected_profile(dispatch_profile)

    if not case_id or not assignment_id:
        await nack(client, message_id, "worker assignment message missing case_id or assignment_id")
        return

    try:
        case_detail = await get_case_detail(client, case_id)
        case = case_detail["case"]
        dispatch_packet = case.get("dispatch_packet_json") or {}
        assignment = dispatch_packet.get("assignment") or {}
        effective_policy = resolve_effective_policy(dispatch_packet)
        case_status = str(case.get("status") or "OPEN").upper()
        packet_assignment_id = str(assignment.get("assignment_id") or "").strip()

        if packet_assignment_id and packet_assignment_id != assignment_id:
            raise RuntimeError(
                f"assignment_id mismatch for case {case_id}: packet={packet_assignment_id} message={assignment_id}"
            )

        if case_status in {"IN_PROGRESS", "COMPLETED", "FAILED"}:
            await append_case_log_safe(
                client,
                case_id,
                "dispatch",
                "duplicate worker assignment ignored",
                metadata={
                    "assignment_id": assignment_id,
                    "dispatch_profile": dispatch_profile,
                    "selected_profile": selected_profile,
                    "case_status": case_status,
                },
            )
            await ack(
                client,
                message_id,
                {
                    "case_id": case_id,
                    "assignment_id": assignment_id,
                    "selected_profile": selected_profile,
                    "already_active": True,
                },
            )
            return

        await append_case_log_safe(
            client,
            case_id,
            "dispatch",
            "worker assignment claimed",
            metadata={
                "assignment_id": assignment_id,
                "worker_queue_message_id": message_id,
                "worker_id": WORKER_ID,
                "executor": executor,
                "dispatch_profile": dispatch_profile,
            },
        )
        await append_case_log_safe(
            client,
            case_id,
            "dispatch",
            "worker profile resolved",
            metadata={
                "assignment_id": assignment_id,
                "executor": executor,
                "dispatch_profile": dispatch_profile,
                "selected_profile": selected_profile,
                "effective_policy": effective_policy,
            },
        )
        skill_state = prepare_profile_skills(profile)
        await append_case_log_safe(
            client,
            case_id,
            "dispatch",
            "worker skills prepared",
            metadata={
                "assignment_id": assignment_id,
                "dispatch_profile": dispatch_profile,
                "selected_profile": selected_profile,
                **skill_state,
            },
        )

        process, launch_result = launch_hermes_session(
            case_id,
            profile,
            dispatch_packet,
            skill_state["runtime_home"],
        )
        await append_case_log(
            client,
            case_id,
            "dispatch",
            "worker execution started",
            metadata={
                "assignment_id": assignment_id,
                "executor": executor,
                "dispatch_profile": dispatch_profile,
                "selected_profile": selected_profile,
                "worker_id": WORKER_ID,
                "worker_queue_message_id": message_id,
                "effective_policy": effective_policy,
                **skill_state,
                **safe_launch_log_metadata(launch_result),
            },
        )
        _track_background_task(
            asyncio.create_task(
                monitor_worker_session(
                    case_id,
                    assignment_id,
                    selected_profile,
                    skill_state["runtime_home"],
                    launch_result["log_path"],
                    process,
                )
            )
        )
        await update_case_status(client, case_id, "IN_PROGRESS")
        await ack(
            client,
            message_id,
            {
                "case_id": case_id,
                "assignment_id": assignment_id,
                "selected_profile": selected_profile,
                "pid": launch_result["pid"],
            },
        )
        log.info(
            "Worker execution started  case_id=%s  assignment_id=%s  profile=%s  pid=%s",
            case_id,
            assignment_id,
            selected_profile,
            launch_result["pid"],
        )
    except Exception as exc:
        log.exception("Worker assignment failed  message_id=%s  case_id=%s  error=%s", message_id, case_id, exc)
        if case_id:
            await append_case_log_safe(
                client,
                case_id,
                "error",
                "worker assignment failed",
                metadata={
                    "assignment_id": assignment_id,
                    "worker_queue_message_id": message_id,
                    "worker_id": WORKER_ID,
                    "dispatch_profile": dispatch_profile,
                    "error": str(exc),
                },
            )
        await nack(client, message_id, str(exc))


def _extract_event_payload(line: str, current_event: str | None) -> dict[str, Any] | None:
    if current_event != TOPIC or not line.startswith("data:"):
        return None
    raw = line.partition(":")[2].lstrip()
    if raw.startswith("data:"):
        raw = raw.partition(":")[2].lstrip()
    if not raw.startswith("{"):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def subscribe_loop(client: httpx.AsyncClient) -> None:
    url = f"{EVENTBUS_URL}/subscribe"
    current_event: str | None = None
    log.info("Connecting to eventbus  url=%s  topic=%s", url, TOPIC)
    async with client.stream("GET", url, params={"topic": TOPIC}, timeout=None) as resp:
        resp.raise_for_status()
        log.info("Connected to eventbus — waiting for worker queue wakeups")
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                current_event = line.partition(":")[2].strip()
                continue
            event_payload = _extract_event_payload(line, current_event)
            if event_payload is not None:
                payload = event_payload.get("payload") or {}
                queue_name = str(payload.get("queue_name") or "").strip()
                if queue_name and queue_name != WORKER_QUEUE_NAME:
                    continue
                await handle_assignment(client)


def is_expected_eventbus_disconnect(exc: Exception) -> bool:
    return isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError))


async def main() -> None:
    delay = RECONNECT_DELAY
    installed_by_home = sync_repo_skills()
    installed_skills = sorted({skill for skills in installed_by_home.values() for skill in skills})
    if installed_skills:
        log.info(
            "Synced Hermes skills  skills=%s  source=%s  homes=%s",
            ",".join(installed_skills),
            CANONICAL_SKILLS_DIR,
            ",".join(sorted(installed_by_home)),
        )
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await handle_assignment(client)
                await subscribe_loop(client)
                delay = RECONNECT_DELAY
            except Exception as exc:
                if is_expected_eventbus_disconnect(exc):
                    log.warning("Eventbus stream closed; reconnecting  error=%s", exc)
                else:
                    log.exception("Subscription error: %s", exc)
                log.info("Reconnecting in %.1fs", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)


if __name__ == "__main__":
    asyncio.run(main())
