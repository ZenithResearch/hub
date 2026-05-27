"""
Frank — Hub Dispatcher

Subscribes to the eventbus on startup (queue.job.enqueued topic via SSE).
On each event, dequeues from the workspace queue, deterministically creates or
reuses a case, persists a dispatch packet, launches the first runnable wave as
the actual target Hermes profiles, and only then settles the intake queue
message while continuing orchestration in-process.

Required environment variables:
  QUEUE_HTTP_URL       http://queue:8081
  EVENTBUS_URL         http://eventbus:8082
  CASES_HTTP_URL       http://cases:8083
  GATEWAY_HTTP_URL     http://gateway-http:8080

Optional:
  QUEUE_NAME           default: workspace
  RECONNECT_DELAY_S    default: 5
  LOG_LEVEL            default: info
  TERMINAL_CWD         repo root; defaults to /hub inside containers
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from services.cases.contract import collect_process_capabilities
from services.frank.case_pipeline_runner import CasePipelineRunner

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "info").upper(),
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
    force=True,
)
log = logging.getLogger("frank")

QUEUE_URL = os.environ["QUEUE_HTTP_URL"]
QUEUE_NAME = os.environ.get("QUEUE_NAME", "workspace")
EVENTBUS_URL = os.environ["EVENTBUS_URL"]
CASES_URL = os.environ["CASES_HTTP_URL"].rstrip("/")
RECONNECT_DELAY = float(os.environ.get("RECONNECT_DELAY_S", "5"))
NATIVE_RECOVERY_INTERVAL_S = float(os.environ.get("FRANK_NATIVE_RECOVERY_INTERVAL_S", "60"))
WORKER_ID = "frank"
TOPIC = "queue.job.enqueued"
TERMINAL_CWD = Path(os.environ.get("TERMINAL_CWD", "/hub")).resolve()
PROCESS_ROOT = TERMINAL_CWD / "base/ops/processes"
PROCESS_HUBFS_ROOT = Path(os.environ.get("PROCESS_HUBFS_ROOT", "/app/base/ops/processes"))
ROLODEX_INDEX = TERMINAL_CWD / "rolodex/index.yaml"
GATEWAY_HTTP_URL = os.environ.get("GATEWAY_HTTP_URL", "http://gateway-http:8080").rstrip("/")
HERMES_PROFILE_ROOT = Path(
    os.environ.get("HERMES_PROFILE_ROOT", str(TERMINAL_CWD / ".hermes" / "profiles"))
).resolve()
FRANK_EXECUTION_ROOT = Path(
    os.environ.get("FRANK_EXECUTION_ROOT", str(TERMINAL_CWD / ".hermes" / "frank_execution"))
).resolve()
FRANK_STEP_MAX_CONCURRENCY = int(os.environ.get("FRANK_STEP_MAX_CONCURRENCY", "3"))
FRANK_STEP_MAX_ITERATIONS = int(os.environ.get("FRANK_STEP_MAX_ITERATIONS", "40"))
STT_HTTP_URL = os.environ.get("STT_HTTP_URL", "http://stt-http:8765").rstrip("/")
VALID_FRANK_RUNTIMES = {"native_case_pipeline"}
BRIEF_COMPILER_MODEL = os.environ.get("FRANK_MODEL") or os.environ.get("MODEL") or ""
BRIEF_COMPILER_TIMEOUT = float(os.environ.get("FRANK_BRIEF_COMPILER_TIMEOUT_S", "20"))
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
TERMINAL_STEP_STATUSES = {"COMPLETED", "FAILED", "SKIPPED"}
TERMINAL_CASE_STATUSES = {"COMPLETED", "FAILED"}
ACTIVE_CASE_TASKS: dict[str, asyncio.Task[Any]] = {}
BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()

REVIEW_EVENT_MAP = {
    "review_submitted": "process-queued-review.md",
    "mock_review_submitted": "mock-review-submitted.md",
}
TRIGGER_RE = re.compile(r"Trigger:\s*`event_type\s*=\s*([^`]+)`", re.IGNORECASE)


def resolve_frank_runtime() -> str:
    runtime = os.environ.get("FRANK_RUNTIME", "native_case_pipeline").strip().lower() or "native_case_pipeline"
    if runtime not in VALID_FRANK_RUNTIMES:
        raise ValueError(
            f"invalid FRANK_RUNTIME={runtime!r}; expected one of {sorted(VALID_FRANK_RUNTIMES)}"
        )
    return runtime



@dataclass(frozen=True)
class ProcessDefinition:
    event_type: str
    process_name: str
    process_path: str
    path: Path
    source: str


@dataclass(frozen=True)
class SenderContext:
    sender: str
    trust: str
    registry_kind: str | None = None


@dataclass(frozen=True)
class BriefCompilerConfig:
    url: str
    headers: dict[str, str]
    model: str


# ── Queue transport ───────────────────────────────────────────────────────────

async def dequeue(client: httpx.AsyncClient) -> dict[str, Any] | None:
    resp = await client.post(
        f"{QUEUE_URL}/queues/{QUEUE_NAME}/dequeue",
        params={"worker_id": WORKER_ID},
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["message"] if data.get("found") else None


async def ack(client: httpx.AsyncClient, message_id: str, result: dict | None = None) -> None:
    (await client.post(
        f"{QUEUE_URL}/messages/{message_id}/ack",
        json={"result": result or {}},
        timeout=5.0,
    )).raise_for_status()


async def nack(client: httpx.AsyncClient, message_id: str, reason: str) -> None:
    (await client.post(
        f"{QUEUE_URL}/messages/{message_id}/nack",
        json={"reason": reason},
        timeout=5.0,
    )).raise_for_status()


# ── Deterministic process resolution ──────────────────────────────────────────

def resolve_sender_context(sender: str | None) -> SenderContext:
    slug = (sender or "").strip()
    if not slug:
        return SenderContext(sender="", trust="default")
    if not ROLODEX_INDEX.exists():
        return SenderContext(sender=slug, trust="default")
    try:
        data = yaml.safe_load(ROLODEX_INDEX.read_text()) or {}
    except Exception:
        return SenderContext(sender=slug, trust="default")
    if slug in ((data.get("agents") or {}).get("entries") or {}):
        return SenderContext(sender=slug, trust="known", registry_kind="agent")
    if slug in ((data.get("people") or {}).get("entries") or {}):
        return SenderContext(sender=slug, trust="known", registry_kind="person")
    return SenderContext(sender=slug, trust="default")


def _process_definition_from_path(event_type: str, path: Path) -> ProcessDefinition:
    source = path.read_text()
    return ProcessDefinition(
        event_type=event_type,
        process_name=event_type,
        process_path=str(PROCESS_HUBFS_ROOT / path.name),
        path=path,
        source=source,
    )


def resolve_process_definition(msg: dict[str, Any]) -> ProcessDefinition:
    event_type = str(msg.get("event_type") or "").strip()
    if not event_type:
        raise ValueError("message is missing event_type")

    mapped_name = REVIEW_EVENT_MAP.get(event_type)
    if mapped_name:
        mapped_path = PROCESS_ROOT / mapped_name
        if mapped_path.exists():
            return _process_definition_from_path(event_type, mapped_path)

    for path in sorted(PROCESS_ROOT.glob("*.md")):
        try:
            source = path.read_text()
        except OSError:
            continue
        match = TRIGGER_RE.search(source)
        if match and match.group(1).strip() == event_type:
            return ProcessDefinition(
                event_type=event_type,
                process_name=event_type,
                process_path=str(PROCESS_HUBFS_ROOT / path.name),
                path=path,
                source=source,
            )

    raise ValueError(f"no process definition found for event_type={event_type}")


def determine_objective(msg: dict[str, Any]) -> str:
    payload = msg.get("payload") or {}
    for key in ("review_id", "request_id", "objective"):
        if payload.get(key):
            return str(payload[key])
    body = str(msg.get("message_body") or "").strip()
    if body:
        return body
    event_type = str(msg.get("event_type") or "request")
    return f"{event_type} from {msg.get('sender') or 'unknown'}"


def resolve_executor_slug(contract: dict[str, Any]) -> str | None:
    executors = [str(step.get("executor")).strip() for step in contract.get("steps", []) if step.get("executor")]
    return executors[0] if executors else None


def build_initial_context(msg: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    payload = msg.get("payload") or {}
    slot_names = set(contract.get("slot_names") or [])
    values: dict[str, Any] = {}
    assets = payload.get("assets") or []
    typed_assets: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_type = str(asset.get("asset_type") or "").strip().lower()
        asset_id = str(asset.get("asset_id") or "").strip()
        if asset_type and asset_id:
            typed_assets[asset_type] = asset_id

    direct_keys = (
        "review_id",
        "subject_id",
        "submitted_by",
        "duration_ms",
        "reviewed_at",
        "audio_asset_id",
        "events_asset_id",
        "audio_asset_path",
    )
    for key in direct_keys:
        if key in slot_names and payload.get(key) is not None:
            values[key] = payload[key]

    if "review_id" in slot_names and values.get("review_id") is None and msg.get("message_body"):
        values["review_id"] = msg["message_body"]

    if "reviewed_at" in slot_names and values.get("reviewed_at") is None and payload.get("stopped_at"):
        values["reviewed_at"] = payload["stopped_at"]

    asset_ids = payload.get("asset_ids") or []
    if "events_asset_id" in slot_names and values.get("events_asset_id") is None and typed_assets.get("events"):
        values["events_asset_id"] = typed_assets["events"]
    if "audio_asset_id" in slot_names and values.get("audio_asset_id") is None and typed_assets.get("audio"):
        values["audio_asset_id"] = typed_assets["audio"]
    if "events_asset_id" in slot_names and values.get("events_asset_id") is None and len(asset_ids) > 0:
        values["events_asset_id"] = asset_ids[0]
    if "audio_asset_id" in slot_names and values.get("audio_asset_id") is None and len(asset_ids) > 1:
        values["audio_asset_id"] = asset_ids[1]
    if "audio_asset_path" in slot_names and values.get("audio_asset_path") is None and len(asset_ids) > 1:
        values["audio_asset_path"] = f"data/reviews/assets/{asset_ids[1]}"

    if "review_id_short" in slot_names and payload.get("review_id"):
        values["review_id_short"] = str(payload["review_id"])[:8]

    for slot_name in slot_names:
        if slot_name not in values and payload.get(slot_name) is not None:
            values[slot_name] = payload[slot_name]

    return values


def resolve_dispatch_profile(
    msg: dict[str, Any],
    contract: dict[str, Any],
    *,
    frank_override: str | None = None,
) -> dict[str, Any]:
    payload = msg.get("payload") or {}
    upstream_hint = str(payload.get("dispatch_profile_hint") or "").strip() or None
    process_default = str(contract.get("dispatch_profile") or "").strip() or None
    executor_fallback = resolve_executor_slug(contract)
    resolved = frank_override or upstream_hint or process_default or executor_fallback or None
    return {
        "upstream_hint": upstream_hint,
        "process_default": process_default,
        "executor_fallback": executor_fallback,
        "resolved": resolved,
        "selected_by": "frank",
    }


def build_assignment_policy(contract: dict[str, Any]) -> dict[str, Any]:
    required_skills: set[str] = set()
    allowed_tools: set[str] = set()
    resource_scopes: set[str] = set()
    for step in contract.get("steps", []):
        required_skills.update(str(skill).strip() for skill in step.get("skills", []) if str(skill).strip())
        allowed_tools.update(str(tool).strip() for tool in step.get("tools", []) if str(tool).strip())
        resource_scopes.update(str(resource).strip() for resource in step.get("resources", []) if str(resource).strip())
        resource_scopes.update(
            str(resource).strip() for resource in step.get("suggested_resources", []) if str(resource).strip()
        )
    return {
        "required_skills": sorted(required_skills),
        "allowed_tools": sorted(allowed_tools),
        "denied_tools": [],
        "resource_scopes": sorted(resource_scopes),
    }


def build_assignment_id(case_id: str, dispatch_profile: str | None) -> str:
    profile_suffix = dispatch_profile or "default"
    return f"assignment:{case_id}:{profile_suffix}"


def derive_workspace_policy(resources: list[Any], *, case_id: str | None = None) -> str:
    normalized = " ".join(str(item).strip().lower() for item in resources if str(item).strip())
    case_token = case_id or "{case_id}"
    if "review assets workspace" in normalized or "review asset" in normalized:
        return f"dir:/hub/.hermes/frank_execution/{case_token}/assets"
    if any(token in normalized for token in ["subject codebase", "hub repo", "codebase"]):
        return "worktree"
    if any(token in normalized for token in ["vault", "publication", "note synthesis", "notes workspace", "daily note"]):
        return "scratch"
    return "scratch"


def _resolve_workspace_policy_case_id(policy: Any, case_id: str) -> str:
    value = str(policy or "scratch").strip() or "scratch"
    return value.replace("{case_id}", case_id)


def build_process_summary(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": contract.get("title"),
        "description": contract.get("description"),
        "root_inputs": list(contract.get("root_inputs") or []),
        "slot_names": list(contract.get("slot_names") or []),
    }


def build_worker_execution_rules() -> list[str]:
    return [
        "Treat the cases service as the durable source of truth.",
        "Write initial_context values into empty root slots before any step work.",
        "Execute steps in waves. Re-fetch the case before each wave and determine readiness from current slot population.",
        "A step is runnable only when all declared inputs are populated, none of its outputs are populated, it is assigned to you, and it is not terminal.",
        "Step 1 is the setup boundary for review cases and should run in the parent worker before any parallel delegation wave.",
        "For later waves, spawn one step runner per runnable step in parallel and pass each runner the exact resolved step brief plus current slot values.",
        "Persist per-step task and runtime state with update_step_runtime_state while work is active.",
        "Do not call set_step_running in this dispatch path.",
        "Step runners must return structured JSON only. The parent worker validates and commits outputs after each wave.",
        "The authoritative child result is an outputs JSON object whose keys match the declared output variable names and whose values match the declared variable types.",
        "Output slot writes committed by the parent are the durable proof of completion for output-producing steps.",
        "Use set_step_completed only for no-output steps.",
        "Slots are write-once; identical rewrites are idempotent, changed rewrites are invalid.",
    ]


def _fallback_step_instructions(step_brief: dict[str, Any]) -> str:
    action = str(step_brief.get("action") or "").strip()
    title = str(step_brief.get("title") or step_brief.get("step_id") or "step").strip()
    outputs = [str(name).strip() for name in step_brief.get("outputs", []) if str(name).strip()]
    if outputs:
        return (
            f"Execute {title}. Produce the declared outputs exactly as named: "
            + ", ".join(outputs)
            + "."
        )
    if action:
        return f"Execute {title} using the step action {action}."
    return f"Execute {title} and respect the declared DAG, inputs, and resources."


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def build_step_briefs(contract: dict[str, Any], case_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    briefs: list[dict[str, Any]] = []
    variables = contract.get("variables") or {}
    for step_row in case_steps:
        contract_step = next(
            (step for step in contract.get("steps", []) if step.get("step_id") == step_row["step_id"]),
            None,
        )
        if contract_step is None:
            continue
        process_instructions = _normalize_text(contract_step.get("instructions"))
        briefs.append(
            {
                "step_db_row_id": step_row["id"],
                "step_id": step_row["step_id"],
                "title": step_row["name"],
                "action": step_row.get("action"),
                "executor": step_row.get("executor"),
                "assignee": contract_step.get("assignee") or step_row.get("assignee"),
                "instructions": process_instructions or _fallback_step_instructions(step_row),
                "process_instructions": process_instructions or None,
                "inputs": contract_step.get("input_items", []),
                "outputs": contract_step.get("output_variables", []),
                "output_schema": {
                    name: {
                        "type": ((variables.get(name) or {}).get("type")),
                        "description": ((variables.get(name) or {}).get("description")),
                    }
                    for name in contract_step.get("output_variables", [])
                },
                "tools": contract_step.get("tools", []),
                "toolsets": contract_step.get("toolsets", []),
                "resources": contract_step.get("resources", []),
                "suggested_resources": contract_step.get("suggested_resources", []),
                "workspace_policy": derive_workspace_policy(
                    list(contract_step.get("resources", []) or [])
                    + list(contract_step.get("suggested_resources", []) or [])
                ),
                "skills": contract_step.get("skills", []),
            }
        )
    return briefs


def resolve_brief_compiler_config() -> BriefCompilerConfig | None:
    model = BRIEF_COMPILER_MODEL.strip()
    if not model:
        return None
    if OPENAI_BASE_URL:
        headers = {"Content-Type": "application/json"}
        if OPENAI_API_KEY and OPENAI_API_KEY.lower() != "none":
            headers["Authorization"] = f"Bearer {OPENAI_API_KEY}"
        return BriefCompilerConfig(
            url=f"{OPENAI_BASE_URL}/chat/completions",
            headers=headers,
            model=model,
        )
    if OPENROUTER_API_KEY:
        return BriefCompilerConfig(
            url=f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            },
            model=model,
        )
    return None


def build_default_resolved_step_briefs(step_briefs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for step_brief in step_briefs:
        resolved.append(
            {
                **step_brief,
                "instructions": _normalize_text(step_brief.get("process_instructions"))
                or _normalize_text(step_brief.get("instructions"))
                or _fallback_step_instructions(step_brief),
                "instruction_source": "process" if step_brief.get("process_instructions") else "fallback",
                "tasking_guidance": [],
            }
        )
    return resolved


def build_default_worker_instructions() -> list[str]:
    return [
        "Fetch the case from the cases service and treat it as the durable source of truth.",
        "Use the process summary and resolved step briefs as the authoritative execution brief for this case.",
        "Before executing steps, write the initial payload-derived slot values from initial_context.",
        "Follow the DAG exactly. Only work on steps that are currently runnable and assigned to you.",
        "Run Step 1 in the parent worker as the setup boundary before starting later waves.",
        "Break each step into tasks locally before delegating or executing.",
        "Persist per-step task/runtime state while work is active.",
        "For delegated steps, require a structured JSON result whose outputs keys and types match the resolved step brief.",
        "The parent worker commits validated outputs durably after each wave; child step runners do not write case slots directly.",
    ]


def build_default_dispatch_brief(contract: dict[str, Any], step_briefs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "process_summary": build_process_summary(contract),
        "resolved_step_briefs": build_default_resolved_step_briefs(step_briefs),
        "worker_execution_rules": build_worker_execution_rules(),
    }


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        for part in parts:
            candidate = part.strip()
            if not candidate:
                continue
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                text = candidate
                break
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("brief compiler response did not contain a JSON object")
        text = match.group(0)
    return json.loads(text)


async def _run_brief_compiler_prompt(
    client: httpx.AsyncClient,
    contract: dict[str, Any],
    step_briefs: list[dict[str, Any]],
) -> dict[str, Any]:
    config = resolve_brief_compiler_config()
    if config is None:
        raise RuntimeError("brief compiler model is not configured")

    payload = {
        "model": config.model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You compile worker execution briefs for process-driven cases. "
                    "Return JSON only. Preserve the DAG, executors, slot names, declared inputs, "
                    "declared outputs, and step ordering. Normalize or infer step instructions when "
                    "they are weak or missing. Never split or merge steps."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": (
                            "Compile a concise process summary, resolved step instructions, and worker "
                            "execution rules for this case. The resolved step briefs must align 1:1 "
                            "with the provided step_briefs by step_id."
                        ),
                        "required_output_shape": {
                            "process_summary": {
                                "execution_summary": "string",
                            },
                            "resolved_step_briefs": [
                                {
                                    "step_id": "string",
                                    "instructions": "string",
                                    "tasking_guidance": ["string"],
                                }
                            ],
                            "worker_execution_rules": ["string"],
                        },
                        "process_summary": build_process_summary(contract),
                        "step_briefs": step_briefs,
                    },
                    indent=2,
                    sort_keys=True,
                ),
            },
        ],
    }
    response = await client.post(
        config.url,
        headers=config.headers,
        json=payload,
        timeout=BRIEF_COMPILER_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    content = (
        (((data.get("choices") or [{}])[0]).get("message") or {}).get("content")
        if isinstance(data, dict)
        else None
    )
    if not content:
        raise ValueError("brief compiler response was empty")
    return _extract_json_object(content)


def _merge_compiled_dispatch_brief(
    contract: dict[str, Any],
    step_briefs: list[dict[str, Any]],
    compiled: dict[str, Any] | None,
) -> dict[str, Any]:
    default_bundle = build_default_dispatch_brief(contract, step_briefs)
    if not compiled:
        return default_bundle

    default_summary = default_bundle["process_summary"]
    compiled_summary = compiled.get("process_summary") if isinstance(compiled.get("process_summary"), dict) else {}
    process_summary = {
        **default_summary,
        **{
            key: value
            for key, value in compiled_summary.items()
            if isinstance(value, (str, list, dict)) and value not in ("", [], {})
        },
    }

    compiled_by_step_id: dict[str, dict[str, Any]] = {}
    for item in compiled.get("resolved_step_briefs") or []:
        if not isinstance(item, dict):
            continue
        step_id = _normalize_text(item.get("step_id"))
        if step_id:
            compiled_by_step_id[step_id] = item

    resolved_step_briefs: list[dict[str, Any]] = []
    for step_brief in step_briefs:
        compiled_step = compiled_by_step_id.get(step_brief["step_id"], {})
        compiled_instructions = _normalize_text(compiled_step.get("instructions"))
        tasking_guidance = [
            _normalize_text(item)
            for item in compiled_step.get("tasking_guidance", [])
            if _normalize_text(item)
        ]
        instruction_source = "compiled"
        if not compiled_instructions:
            compiled_instructions = (
                _normalize_text(step_brief.get("process_instructions"))
                or _normalize_text(step_brief.get("instructions"))
                or _fallback_step_instructions(step_brief)
            )
            instruction_source = "process" if step_brief.get("process_instructions") else "fallback"
        resolved_step_briefs.append(
            {
                **step_brief,
                "instructions": compiled_instructions,
                "instruction_source": instruction_source,
                "tasking_guidance": tasking_guidance,
            }
        )

    rules = default_bundle["worker_execution_rules"][:]
    for item in compiled.get("worker_execution_rules") or []:
        normalized = _normalize_text(item)
        if normalized and normalized not in rules:
            rules.append(normalized)

    return {
        "process_summary": process_summary,
        "resolved_step_briefs": resolved_step_briefs,
        "worker_execution_rules": rules,
    }


async def compile_dispatch_brief(
    client: httpx.AsyncClient,
    contract: dict[str, Any],
    step_briefs: list[dict[str, Any]],
) -> dict[str, Any]:
    if resolve_brief_compiler_config() is None:
        return build_default_dispatch_brief(contract, step_briefs)
    try:
        compiled = await _run_brief_compiler_prompt(client, contract, step_briefs)
    except Exception as exc:
        log.warning("Brief compiler fallback engaged  error=%s", exc)
        compiled = None
    return _merge_compiled_dispatch_brief(contract, step_briefs, compiled)


def build_dispatch_packet(
    msg: dict[str, Any],
    case_payload: dict[str, Any],
    process_def: ProcessDefinition,
    sender_context: SenderContext,
    executor_slug: str | None,
    profile_resolution: dict[str, Any],
    dispatch_brief: dict[str, Any],
) -> dict[str, Any]:
    contract = case_payload["contract"]
    case_id = case_payload["case_id"]
    initial_context = build_initial_context(msg, contract)
    dispatch_profile = profile_resolution["resolved"]
    step_briefs = build_step_briefs(contract, case_payload["steps"])
    step_briefs = [
        {**brief, "workspace_policy": _resolve_workspace_policy_case_id(brief.get("workspace_policy"), case_id)}
        for brief in step_briefs
    ]
    resolved_step_briefs = dispatch_brief.get("resolved_step_briefs") or build_default_resolved_step_briefs(step_briefs)
    resolved_step_briefs = [
        {**brief, "workspace_policy": _resolve_workspace_policy_case_id(brief.get("workspace_policy"), case_id)}
        for brief in resolved_step_briefs
    ]
    worker_execution_rules = dispatch_brief.get("worker_execution_rules") or build_worker_execution_rules()
    runtime_mode = resolve_frank_runtime()

    packet = {
        "case_id": case_id,
        "queue_message_id": msg["id"],
        "event_type": msg.get("event_type"),
        "sender": {
            "id": sender_context.sender,
            "trust": sender_context.trust,
            "registry_kind": sender_context.registry_kind,
        },
        "objective": determine_objective(msg),
        "process": {
            "name": process_def.process_name,
            "path": process_def.process_path,
            "source_path": str(process_def.path),
            "hash": contract.get("process_hash"),
            "title": contract.get("title"),
            "dispatch_profile": contract.get("dispatch_profile"),
        },
        "assignment": {
            "assignment_id": build_assignment_id(case_id, dispatch_profile),
            "executor": executor_slug,
            "dispatch_profile": dispatch_profile,
            "queue_name": None,
            "policy": build_assignment_policy(contract),
            "profile_resolution": profile_resolution,
        },
        "initial_context": initial_context,
        "runtime": {
            "mode": runtime_mode,
            "source_of_truth": "cases/Zenith",
        },
        "process_summary": dispatch_brief.get("process_summary") or build_process_summary(contract),
        "steps": step_briefs,
        "step_briefs": step_briefs,
        "resolved_step_briefs": resolved_step_briefs,
        "dag_edges": contract.get("dag_edges", []),
        "capabilities": collect_process_capabilities(contract),
        "worker_instructions": build_default_worker_instructions(),
        "worker_execution_rules": worker_execution_rules,
        "case_execution_rules": worker_execution_rules,
    }
    return packet


# ── Cases service helpers ─────────────────────────────────────────────────────

async def create_case_record(
    client: httpx.AsyncClient,
    msg: dict[str, Any],
    process_def: ProcessDefinition,
) -> dict[str, Any]:
    response = await client.post(
        f"{CASES_URL}/cases",
        json={
            "queue_message_id": msg["id"],
            "process_name": process_def.process_name,
            "process_path": process_def.process_path,
            "process_source": process_def.source,
            "title": f"{msg.get('event_type', 'request')} from {msg.get('sender', 'unknown')}",
            "objective": determine_objective(msg),
            "sender": str(msg.get("sender") or "unknown"),
        },
        timeout=20.0,
    )
    response.raise_for_status()
    return response.json()


async def persist_dispatch_packet(
    client: httpx.AsyncClient,
    case_id: str,
    dispatch_packet: dict[str, Any],
) -> None:
    response = await client.put(
        f"{CASES_URL}/cases/{case_id}/dispatch-packet",
        json={"dispatch_packet_json": dispatch_packet},
        timeout=10.0,
    )
    response.raise_for_status()


REVIEW_ROOT_CONTEXT_SLOT_NAMES = (
    "review_id",
    "audio_asset_id",
    "events_asset_id",
    "subject_id",
    "submitted_by",
    "reviewed_at",
    "duration_ms",
)


def _slot_value_is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


async def write_root_context_slots(
    client: httpx.AsyncClient,
    case_id: str,
    dispatch_packet: dict[str, Any],
    case_detail: dict[str, Any],
) -> dict[str, list[str]]:
    """Write payload-derived review root context slots exactly once.

    Root context belongs on case slots, not Step 1 outputs. This helper is
    intentionally conservative: it skips declared root slots that already have a
    non-empty value in the fetched case detail rather than relying on the slots
    endpoint to reject changed rewrites.
    """
    initial_context = dispatch_packet.get("initial_context") or {}
    declared_slots = set((case_detail.get("contract") or {}).get("slot_names") or [])
    existing_values = slot_values_by_name(case_detail)
    written: list[str] = []
    skipped_existing: list[str] = []
    skipped_missing: list[str] = []

    for name in REVIEW_ROOT_CONTEXT_SLOT_NAMES:
        if declared_slots and name not in declared_slots:
            skipped_missing.append(name)
            continue
        value = initial_context.get(name)
        if not _slot_value_is_non_empty(value):
            skipped_missing.append(name)
            continue
        if _slot_value_is_non_empty(existing_values.get(name)):
            skipped_existing.append(name)
            continue
        response = await client.post(
            f"{CASES_URL}/cases/{case_id}/slots",
            json={"name": name, "value": value, "agent_run_id": None},
            timeout=10.0,
        )
        response.raise_for_status()
        written.append(name)

    return {
        "written": written,
        "skipped_existing": skipped_existing,
        "skipped_missing": skipped_missing,
    }


async def append_case_log(
    client: httpx.AsyncClient,
    case_id: str,
    log_type: str,
    message: str,
    *,
    step_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    response = await client.post(
        f"{CASES_URL}/cases/{case_id}/logs",
        json={
            "step_id": step_id,
            "type": log_type,
            "message": message,
            "metadata": metadata or {},
        },
        timeout=10.0,
    )
    response.raise_for_status()


async def append_case_log_safe(
    client: httpx.AsyncClient,
    case_id: str,
    log_type: str,
    message: str,
    *,
    step_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    try:
        await append_case_log(client, case_id, log_type, message, step_id=step_id, metadata=metadata)
    except Exception as exc:
        log.warning("Failed to append case log  case_id=%s  error=%s", case_id, exc)


# ── Queue + cases helpers ─────────────────────────────────────────────────────

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











def _deserialize_slot_value(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list, int, float, bool)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def slot_values_by_name(case_detail: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for slot in case_detail.get("slots", []):
        values[str(slot["name"])] = _deserialize_slot_value(slot.get("value"))
    return values










def ensure_case_runtime_dir(case_id: str) -> Path:
    case_dir = FRANK_EXECUTION_ROOT / case_id
    (case_dir / "waves").mkdir(parents=True, exist_ok=True)
    (case_dir / "steps").mkdir(parents=True, exist_ok=True)
    (case_dir / "sessions").mkdir(parents=True, exist_ok=True)
    (case_dir / "logs").mkdir(parents=True, exist_ok=True)
    (case_dir / "results").mkdir(parents=True, exist_ok=True)
    (case_dir / "assets").mkdir(parents=True, exist_ok=True)
    return case_dir




def read_config_secret_file() -> dict[str, str]:
    path = os.environ.get("HUB_CONFIG_SECRETS_PATH", "").strip()
    if not path:
        return {}
    secret_path = Path(path)
    if not secret_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in secret_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def missing_capability_env_vars(capabilities: dict[str, Any]) -> list[str]:
    secret_values = read_config_secret_file()
    missing: list[str] = []
    for env_var in capabilities.get("env_vars") or []:
        key = str(env_var).strip()
        if key and not os.environ.get(key) and not secret_values.get(key):
            missing.append(key)
    return missing




































def _case_has_durable_active_steps(case_detail: dict[str, Any]) -> bool:
    for step in case_detail.get("steps") or []:
        status = str(step.get("status") or "").upper()
        runtime_state = step.get("runtime_state_json") or {}
        runtime_status = str(runtime_state.get("status") or "").lower() if isinstance(runtime_state, dict) else ""
        if status in {"RUNNING", "IN_PROGRESS"} or runtime_status == "active":
            return True
    return False






async def execute_native_case_pipeline(case_id: str, dispatch_packet: dict[str, Any], case_dir: Path) -> None:
    async with httpx.AsyncClient() as client:
        runner = CasePipelineRunner(
            client=client,
            cases_url=CASES_URL,
            gateway_url=GATEWAY_HTTP_URL,
            stt_url=STT_HTTP_URL,
            execution_root=case_dir.parent,
        )
        result = await runner.run(case_id, dispatch_packet)
        await append_case_log_safe(
            client,
            case_id,
            "native_case_pipeline",
            "native case pipeline finished",
            metadata={
                "case_run_id": result.case_run_id,
                "status": result.status,
                "completed_step_ids": list(result.completed_step_ids),
                "blocked_step_id": result.blocked_step_id,
                "blocked_reason": result.blocked_reason,
            },
        )


def _native_case_task_done_callback(case_id: str):
    def _done(task: asyncio.Task[Any]) -> None:
        ACTIVE_CASE_TASKS.pop(case_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            log.info("Native case pipeline task cancelled  case_id=%s", case_id)
        except Exception as exc:
            log.exception("Native case pipeline task failed  case_id=%s  error=%s", case_id, exc)

    return _done


def _dispatch_packet_from_case_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("dispatch_packet_json")
    if raw is None:
        raw = payload.get("dispatch_packet")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _is_native_case_pipeline_packet(dispatch_packet: dict[str, Any]) -> bool:
    runtime_mode = str((dispatch_packet.get("runtime") or {}).get("mode") or "").strip().lower()
    return runtime_mode == "native_case_pipeline"


async def schedule_native_case_pipeline_task(
    client: httpx.AsyncClient,
    case_id: str,
    dispatch_packet: dict[str, Any],
    case_dir: Path,
    *,
    reason: str,
) -> bool:
    if case_id in ACTIVE_CASE_TASKS:
        return False
    task = asyncio.create_task(execute_native_case_pipeline(case_id, dispatch_packet, case_dir))
    ACTIVE_CASE_TASKS[case_id] = task
    task.add_done_callback(_native_case_task_done_callback(case_id))
    await append_case_log_safe(
        client,
        case_id,
        "native_case_pipeline",
        f"native case pipeline {reason}",
        metadata={"runtime_mode": "native_case_pipeline", "case_dir": str(case_dir)},
    )
    return True


async def recover_native_case_pipelines(client: httpx.AsyncClient, *, limit: int = 100, reason: str = "recovered after Frank startup") -> dict[str, Any]:
    """Recover native case runs that have no durable active runner.

    This covers the production failure mode where Frank claimed/acked a queue
    message, wrote root slots, and logged "native case pipeline scheduled", but
    the in-process asyncio task was lost before Step 1 wrote any durable progress.
    """
    recovered: list[str] = []
    inspected: set[str] = set()
    for status in ("IN_PROGRESS", "OPEN"):
        response = await client.get(f"{CASES_URL}/cases", params={"status": status, "limit": limit}, timeout=10.0)
        response.raise_for_status()
        for summary in response.json().get("cases") or []:
            if not isinstance(summary, dict):
                continue
            case_id = str(summary.get("id") or summary.get("case_id") or "").strip()
            if not case_id or case_id in inspected:
                continue
            inspected.add(case_id)
            dispatch_packet = _dispatch_packet_from_case_payload(summary)
            detail: dict[str, Any] | None = None
            if not dispatch_packet or status == "IN_PROGRESS":
                detail = await get_case_detail(client, case_id)
                dispatch_packet = dispatch_packet or _dispatch_packet_from_case_payload(detail.get("case") or detail)
            if not _is_native_case_pipeline_packet(dispatch_packet):
                continue
            if detail is not None and _case_has_durable_active_steps(detail):
                continue
            case_dir = ensure_case_runtime_dir(case_id)
            if await schedule_native_case_pipeline_task(client, case_id, dispatch_packet, case_dir, reason=reason):
                recovered.append(case_id)
    return {"recovered_case_ids": recovered, "recovered_count": len(recovered)}


async def launch_case_native_pipeline_execution(
    client: httpx.AsyncClient,
    case_id: str,
    dispatch_packet: dict[str, Any],
    case_detail: dict[str, Any],
    case_dir: Path,
) -> dict[str, Any]:
    root_slot_state = await write_root_context_slots(client, case_id, dispatch_packet, case_detail)
    runner = CasePipelineRunner(
        client=client,
        cases_url=CASES_URL,
        gateway_url=GATEWAY_HTTP_URL,
        stt_url=STT_HTTP_URL,
        execution_root=case_dir.parent,
    )
    case_run = await runner.create_case_run(case_id, dispatch_packet)
    await schedule_native_case_pipeline_task(client, case_id, dispatch_packet, case_dir, reason="scheduled")
    await append_case_log_safe(
        client,
        case_id,
        "native_case_pipeline",
        "native case pipeline scheduled",
        metadata={
            "runtime_mode": "native_case_pipeline",
            "case_run_id": case_run["id"],
            "case_dir": str(case_dir),
            "root_slots": root_slot_state,
        },
    )
    return {
        "case_id": case_id,
        "already_active": False,
        "case_dir": str(case_dir),
        "runtime_mode": "native_case_pipeline",
        "case_run_id": case_run["id"],
        "root_slots": root_slot_state,
        "launched_steps": [],
        "scheduled": True,
    }


async def start_case_execution(
    client: httpx.AsyncClient,
    case_id: str,
    dispatch_packet: dict[str, Any],
) -> dict[str, Any]:
    if case_id in ACTIVE_CASE_TASKS:
        return {"case_id": case_id, "already_active": True, "launched_steps": []}

    case_dir = ensure_case_runtime_dir(case_id)
    case_detail = await get_case_detail(client, case_id)
    runtime_mode = str((dispatch_packet.get("runtime") or {}).get("mode") or resolve_frank_runtime()).strip().lower()
    if runtime_mode not in VALID_FRANK_RUNTIMES:
        raise ValueError(
            f"invalid dispatch runtime mode={runtime_mode!r}; expected one of {sorted(VALID_FRANK_RUNTIMES)}"
        )
    if _case_has_durable_active_steps(case_detail):
        await append_case_log_safe(
            client,
            case_id,
            "info",
            "durable active step runtime found; not launching duplicate runners",
            metadata={"active_step_ids": [step.get("id") for step in case_detail.get("steps") or []]},
        )
        return {
            "case_id": case_id,
            "already_active": True,
            "case_dir": str(case_dir),
            "wave_id": None,
            "launched_steps": [],
        }

    missing_env = missing_capability_env_vars(dispatch_packet.get("capabilities") or {})
    if missing_env:
        reason = "missing required configuration: " + ", ".join(missing_env)
        await append_case_log_safe(
            client,
            case_id,
            "warning",
            reason,
            metadata={"missing_env_vars": missing_env, "configuration_surface": "gateway /dashboard configuration"},
        )
        await update_case_status(client, case_id, "BLOCKED")
        return {
            "case_id": case_id,
            "already_active": False,
            "case_dir": str(case_dir),
            "wave_id": None,
            "launched_steps": [],
            "blocked_reason": reason,
        }

    if runtime_mode == "native_case_pipeline":
        return await launch_case_native_pipeline_execution(client, case_id, dispatch_packet, case_detail, case_dir)

    raise ValueError(f"unsupported Frank runtime mode={runtime_mode!r}; only native_case_pipeline is supported")


async def dispatch_message(client: httpx.AsyncClient, msg: dict[str, Any]) -> dict[str, Any]:
    process_def = resolve_process_definition(msg)
    case_payload = await create_case_record(client, msg, process_def)
    case_id = case_payload["case_id"]
    case_detail = await get_case_detail(client, case_id)
    existing_packet = (case_detail.get("case") or {}).get("dispatch_packet_json") or case_detail.get("dispatch_packet") or {}
    existing_assignment = existing_packet.get("assignment") or {}

    if case_payload.get("reused") and existing_assignment.get("assignment_id"):
        result = await start_case_execution(client, case_id, existing_packet or {"case_id": case_id, "capabilities": {}})
        result["assignment_id"] = existing_assignment["assignment_id"]
        return result

    sender_context = resolve_sender_context(msg.get("sender"))
    contract = case_payload["contract"]
    executor_slug = resolve_executor_slug(contract)
    profile_resolution = resolve_dispatch_profile(msg, contract)
    step_briefs = build_step_briefs(contract, case_payload["steps"])
    dispatch_brief = await compile_dispatch_brief(client, contract, step_briefs)
    dispatch_packet = build_dispatch_packet(
        msg,
        case_payload,
        process_def,
        sender_context,
        executor_slug,
        profile_resolution,
        dispatch_brief,
    )
    await persist_dispatch_packet(client, case_id, dispatch_packet)
    case_detail = await get_case_detail(client, case_id)
    await write_root_context_slots(client, case_id, dispatch_packet, case_detail)
    return await start_case_execution(client, case_id, dispatch_packet)


def is_expected_eventbus_disconnect(exc: Exception) -> bool:
    return isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError))


async def handle_enqueued(client: httpx.AsyncClient) -> None:
    msg = await dequeue(client)
    if msg is None:
        return
    msg_id = msg["id"]
    try:
        result = await dispatch_message(client, msg)
    except Exception as exc:
        log.exception("Deterministic dispatch failed  msg_id=%s  error=%s", msg_id, exc)
        await nack(client, msg_id, str(exc))
        return
    await ack(client, msg_id, result)


async def subscribe_loop(client: httpx.AsyncClient) -> None:
    url = f"{EVENTBUS_URL}/subscribe"
    async with client.stream("GET", url, params={"topic": TOPIC}, timeout=None) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("event:") and TOPIC in line:
                await handle_enqueued(client)


async def run_native_case_recovery_tick(client: httpx.AsyncClient) -> dict[str, Any]:
    result = await recover_native_case_pipelines(client, reason="recovered by Frank watchdog")
    if result.get("recovered_count"):
        log.warning("Recovered stale native case pipelines  case_ids=%s", result.get("recovered_case_ids"))
    return result


async def native_case_pipeline_recovery_watchdog(client: httpx.AsyncClient) -> None:
    while True:
        await asyncio.sleep(NATIVE_RECOVERY_INTERVAL_S)
        try:
            await run_native_case_recovery_tick(client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Native case pipeline watchdog recovery failed  error=%s", exc)


async def main() -> None:
    delay = RECONNECT_DELAY
    async with httpx.AsyncClient() as client:
        watchdog_task = asyncio.create_task(native_case_pipeline_recovery_watchdog(client))
        BACKGROUND_TASKS.add(watchdog_task)
        watchdog_task.add_done_callback(BACKGROUND_TASKS.discard)
        try:
            recovered = await recover_native_case_pipelines(client)
            if recovered["recovered_count"]:
                log.info("Recovered native case pipelines  case_ids=%s", recovered["recovered_case_ids"])
        except Exception as exc:
            log.exception("Native case pipeline recovery failed  error=%s", exc)
        while True:
            try:
                await subscribe_loop(client)
                delay = RECONNECT_DELAY
            except Exception as exc:
                if not is_expected_eventbus_disconnect(exc):
                    log.exception("Subscription error: %s", exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)


if __name__ == "__main__":
    asyncio.run(main())
