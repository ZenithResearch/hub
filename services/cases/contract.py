from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

import yaml

from libs.common.errors import NotFoundAppError, ValidationAppError
from libs.tools.registry import ToolRegistry


class ProcessContractError(ValueError):
    """Raised when a process doc cannot be compiled into a valid contract."""


def compile_process_contract(source: str, *, process_path: str | None = None) -> dict[str, Any]:
    if not source.strip():
        raise ProcessContractError("process_source is required to compile a case contract")

    frontmatter, body_source = _split_frontmatter(source)
    lines = body_source.splitlines()
    variables, variable_order = _extract_variables(lines)
    process_env_vars = _extract_process_environment(lines)

    title = ""
    description = ""
    steps: list[dict[str, Any]] = []

    mode = "preamble"
    desc_buf: list[str] = []
    step_num = 0
    step_title = ""
    instr_buf: list[str] = []
    input_buf: list[str] = []
    parsed_outputs: list[dict[str, Any]] = []
    current_output_name = ""
    current_output_content_buf: list[str] = []
    skills: list[str] = []
    resources: list[str] = []
    suggested_resources: list[str] = []
    tools: list[str] = []
    toolsets: list[str] = []
    executor: str | None = None
    assignee: str | None = None
    current_field = "none"
    produced_by: dict[str, int] = {}
    in_fence = False

    def build_input_items(raw_inputs: str) -> list[dict[str, Any]]:
        names = _extract_backtick_names(raw_inputs)
        items: list[dict[str, Any]] = []
        for name in names:
            items.append(_variable_item(name, variables))
        return items

    def flush_output_item() -> None:
        nonlocal current_output_name, current_output_content_buf
        if not current_output_name:
            return

        content = "\n".join(current_output_content_buf).strip()
        output_name = current_output_name
        lower_name = output_name.lower()
        output_items: list[dict[str, Any]] = []

        if output_name in variables:
            output_items.append(_variable_item(output_name, variables))
        else:
            keys = _extract_json_keys(content) if "process state" in lower_name else []
            if keys:
                for key in keys:
                    output_items.append(_variable_item(key, variables))
            else:
                raise ProcessContractError(
                    "Deprecated non-process-state output "
                    f"{output_name!r} in Step {step_num}. "
                    "Use Required Resources:/Suggested Resources:/Tools: for capabilities "
                    "and keep Output (process state) for slot-backed variables only."
                )

        seen_step_outputs = {item["name"] for item in parsed_outputs}
        for item in output_items:
            if item["name"] in seen_step_outputs:
                raise ProcessContractError(
                    f"Step {step_num} declares {item['name']} more than once in its outputs"
                )
            seen_step_outputs.add(item["name"])
            parsed_outputs.append(item)

        current_output_name = ""
        current_output_content_buf = []

    def flush_step() -> None:
        nonlocal parsed_outputs, current_output_name, current_output_content_buf
        if step_num <= 0:
            return

        flush_output_item()
        raw_inputs = "\n".join(input_buf).strip()
        input_items = build_input_items(raw_inputs)
        instructions = "\n".join(instr_buf).strip()
        if not instructions:
            raise ProcessContractError(
                f"Step {step_num} must declare **Processing:** instructions"
            )
        output_variables = [item["name"] for item in parsed_outputs if item["name"] in variables]
        for name in output_variables:
            if name in produced_by:
                raise ProcessContractError(
                    f"Variable {name} is produced by both Step {produced_by[name]} and Step {step_num}"
                )
            produced_by[name] = step_num

        action = skills[0] if skills else step_title
        steps.append(
            {
                "id": step_num,
                "step_id": f"step_{step_num}",
                "number": step_num,
                "title": step_title,
                "instructions": instructions,
                "inputs": raw_inputs,
                "input_items": input_items,
                "outputs": parsed_outputs,
                "output_variables": output_variables,
                "skills": list(skills),
                "resources": list(resources),
                "suggested_resources": list(suggested_resources),
                "tools": list(tools),
                "toolsets": list(toolsets),
                "executor": executor,
                "assignee": assignee,
                "action": action,
            }
        )

    def reset_step() -> None:
        nonlocal instr_buf, input_buf, parsed_outputs, current_output_name, current_output_content_buf
        nonlocal skills, resources, suggested_resources, tools, toolsets, executor, assignee, current_field
        instr_buf = []
        input_buf = []
        parsed_outputs = []
        current_output_name = ""
        current_output_content_buf = []
        skills = []
        resources = []
        suggested_resources = []
        tools = []
        toolsets = []
        executor = None
        assignee = None
        current_field = "none"

    for line in lines:
        trimmed = line.strip()

        if trimmed.startswith("```"):
            if mode == "description":
                desc_buf.append(line)
            elif mode == "step_body":
                if current_field == "input":
                    input_buf.append(trimmed)
                elif current_field == "output":
                    current_output_content_buf.append(line)
                elif current_field == "skill":
                    if trimmed:
                        skills.append(_normalize_declared_capability(trimmed))
                elif current_field == "resource":
                    if trimmed:
                        resources.append(_normalize_declared_capability(trimmed))
                elif current_field == "suggested_resource":
                    if trimmed:
                        suggested_resources.append(_normalize_declared_capability(trimmed))
                elif current_field == "tool":
                    if trimmed:
                        tools.append(_normalize_declared_capability(trimmed))
                elif current_field == "toolset":
                    if trimmed:
                        toolsets.append(_normalize_declared_capability(trimmed))
                else:
                    instr_buf.append(trimmed)
            in_fence = not in_fence
            continue

        if in_fence:
            if mode == "description":
                desc_buf.append(line)
            elif mode == "step_body":
                if current_field == "input":
                    input_buf.append(trimmed)
                elif current_field == "output":
                    current_output_content_buf.append(line)
                elif current_field == "skill":
                    if trimmed:
                        skills.append(_normalize_declared_capability(trimmed))
                elif current_field == "resource":
                    if trimmed:
                        resources.append(_normalize_declared_capability(trimmed))
                elif current_field == "suggested_resource":
                    if trimmed:
                        suggested_resources.append(_normalize_declared_capability(trimmed))
                elif current_field == "tool":
                    if trimmed:
                        tools.append(_normalize_declared_capability(trimmed))
                elif current_field == "toolset":
                    if trimmed:
                        toolsets.append(_normalize_declared_capability(trimmed))
                else:
                    instr_buf.append(trimmed)
            continue

        if trimmed.startswith("# ") and not trimmed.startswith("## ") and not title:
            title = trimmed[2:]
            continue

        if trimmed == "## What this process does":
            mode = "description"
            continue

        if trimmed == "## Steps":
            mode = "steps"
            continue

        if trimmed.startswith("## ") and mode != "preamble":
            if mode == "step_body":
                flush_step()
                reset_step()
                step_num = 0
                step_title = ""
            mode = "preamble"
            continue

        if mode == "preamble":
            continue

        if mode == "description":
            if trimmed == "---":
                mode = "preamble"
            else:
                desc_buf.append(line)
            continue

        if mode == "steps":
            if trimmed.startswith("### Step "):
                flush_step()
                step_num, step_title = _parse_step_header(trimmed, step_num)
                reset_step()
                mode = "step_body"
            continue

        if mode != "step_body":
            continue

        if trimmed.startswith("### Step "):
            flush_step()
            step_num, step_title = _parse_step_header(trimmed, step_num)
            reset_step()
            continue

        if trimmed.startswith("## "):
            flush_step()
            mode = "preamble"
            continue

        if trimmed == "---":
            current_field = "none"
            continue

        lower = trimmed.lower()
        if lower.startswith("**input"):
            current_field = "input"
            rest = _strip_bold_label(trimmed)
            if rest:
                input_buf.append(rest)
            continue
        if lower.startswith("**processing"):
            current_field = "none"
            rest = _strip_bold_label(trimmed)
            if rest:
                instr_buf.append(rest)
            continue
        if lower.startswith("**instructions"):
            current_field = "none"
            rest = _strip_bold_label(trimmed)
            if rest:
                instr_buf.append(rest)
            continue
        if lower.startswith("**output"):
            flush_output_item()
            current_output_name = _extract_output_name(trimmed)
            current_field = "output"
            rest = _strip_bold_label(trimmed)
            if rest:
                current_output_content_buf.append(rest)
            continue
        if lower.startswith("**skill"):
            current_field = "skill"
            rest = _strip_bold_label(trimmed)
            if rest:
                skills.append(_normalize_declared_capability(rest))
            continue
        if lower.startswith("**executor"):
            current_field = "none"
            rest = _strip_bold_label(trimmed)
            if rest:
                executor = _normalize_declared_capability(rest)
            continue
        if lower.startswith("**assignee"):
            current_field = "none"
            rest = _strip_bold_label(trimmed)
            if rest:
                assignee = _normalize_declared_capability(rest)
            continue
        if lower.startswith("**required resource"):
            current_field = "resource"
            rest = _strip_bold_label(trimmed)
            if rest:
                resources.append(_normalize_declared_capability(rest))
            continue
        if lower.startswith("**suggested resource"):
            current_field = "suggested_resource"
            rest = _strip_bold_label(trimmed)
            if rest:
                suggested_resources.append(_normalize_declared_capability(rest))
            continue
        if lower.startswith("**resource"):
            current_field = "resource"
            rest = _strip_bold_label(trimmed)
            if rest:
                resources.append(_normalize_declared_capability(rest))
            continue
        if lower.startswith("**suggested toolset"):
            current_field = "toolset"
            rest = _strip_bold_label(trimmed)
            if rest:
                toolsets.append(_normalize_declared_capability(rest))
            continue
        if lower.startswith("**toolset"):
            current_field = "toolset"
            rest = _strip_bold_label(trimmed)
            if rest:
                toolsets.append(_normalize_declared_capability(rest))
            continue
        if lower.startswith("**tool"):
            current_field = "tool"
            rest = _strip_bold_label(trimmed)
            if rest:
                tools.append(_normalize_declared_capability(rest))
            continue

        if current_field == "input":
            input_buf.append(trimmed)
        elif current_field == "output":
            current_output_content_buf.append(line)
        elif current_field == "skill":
            if trimmed:
                skills.append(_normalize_declared_capability(trimmed))
        elif current_field == "resource":
            if trimmed:
                resources.append(_normalize_declared_capability(trimmed))
        elif current_field == "suggested_resource":
            if trimmed:
                suggested_resources.append(_normalize_declared_capability(trimmed))
        elif current_field == "tool":
            if trimmed:
                tools.append(_normalize_declared_capability(trimmed))
        elif current_field == "toolset":
            if trimmed:
                toolsets.append(_normalize_declared_capability(trimmed))
        else:
            instr_buf.append(trimmed)

    flush_step()

    if not title:
        raise ProcessContractError("process doc is missing a top-level # title")
    if not steps:
        raise ProcessContractError("process doc must declare at least one step")

    _validate_declared_tools(steps)

    description = "\n".join(desc_buf).strip()
    consumer_map = {name: [] for name in variable_order}
    for step in steps:
        for item in step["input_items"]:
            consumer_map[item["name"]].append(step["number"])

    root_inputs = [name for name in variable_order if name not in produced_by]
    edges_by_pair: dict[tuple[int, int], list[str]] = {}
    variable_position = {name: idx for idx, name in enumerate(variable_order)}
    for name, producer_step in produced_by.items():
        for consumer_step in consumer_map[name]:
            pair = (producer_step - 1, consumer_step - 1)
            edges_by_pair.setdefault(pair, []).append(name)

    dag_edges = []
    for pair, names in sorted(edges_by_pair.items()):
        ordered_names = sorted(names, key=lambda name: variable_position[name])
        label = ordered_names[0] if len(ordered_names) == 1 else f"{ordered_names[0]} +{len(ordered_names) - 1}"
        dag_edges.append(
            {
                "from": pair[0],
                "to": pair[1],
                "label": label,
                "is_skip": pair[1] > pair[0] + 1,
                "variables": ordered_names,
            }
        )

    return {
        "version": 1,
        "title": title,
        "description": description,
        "process_path": process_path,
        "process_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "dispatch_profile": _normalize_optional_string(frontmatter.get("dispatch_profile")),
        "slot_names": list(variable_order),
        "root_inputs": root_inputs,
        "variables": variables,
        "producer_map": produced_by,
        "consumer_map": consumer_map,
        "steps": steps,
        "dag_edges": dag_edges,
        "capabilities": {"env_vars": process_env_vars},
    }


def collect_process_capabilities(contract: dict[str, Any]) -> dict[str, list[str]]:
    """Return ordered unique capabilities required by a compiled process contract."""
    registry = _load_tool_registry()
    capabilities: dict[str, list[str]] = {
        "skills": [],
        "resources": [],
        "suggested_resources": [],
        "tools": [],
        "toolsets": [],
        "env_vars": [],
    }
    seen: dict[str, set[str]] = {key: set() for key in capabilities}

    def add(bucket: str, value: Any) -> None:
        text = str(value or "").strip()
        if not text or text in seen[bucket]:
            return
        seen[bucket].add(text)
        capabilities[bucket].append(text)

    for step in contract.get("steps") or []:
        for key in ("skills", "resources", "suggested_resources", "tools", "toolsets"):
            for item in step.get(key) or []:
                add(key, item)
        if registry is not None:
            for tool_name in step.get("tools") or []:
                try:
                    manifest = registry.get(str(tool_name))
                except NotFoundAppError:
                    continue
                for env_var in manifest.env_vars:
                    add("env_vars", env_var)

    contract_capabilities = contract.get("capabilities") or {}
    for env_var in contract_capabilities.get("env_vars") or []:
        add("env_vars", env_var)

    return capabilities


def _split_frontmatter(source: str) -> tuple[dict[str, Any], str]:
    lines = source.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, source

    closing_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            closing_idx = idx
            break

    if closing_idx is None:
        raise ProcessContractError("frontmatter block is missing a closing --- line")

    raw_frontmatter = "\n".join(lines[1:closing_idx]).strip()
    try:
        parsed = yaml.safe_load(raw_frontmatter) if raw_frontmatter else {}
    except yaml.YAMLError as exc:
        raise ProcessContractError(f"invalid process frontmatter: {exc}") from exc
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ProcessContractError("process frontmatter must parse to a mapping")

    return parsed, "\n".join(lines[closing_idx + 1 :])


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _extract_variables(lines: list[str]) -> tuple[dict[str, dict[str, str]], list[str]]:
    variables: dict[str, dict[str, str]] = {}
    order: list[str] = []
    in_section = False
    for line in lines:
        trimmed = line.strip()
        if trimmed == "## Variables":
            in_section = True
            continue
        if not in_section:
            continue
        if trimmed.startswith("## "):
            break
        if not trimmed.startswith("|"):
            continue
        cols = [col.strip() for col in trimmed.split("|") if col.strip()]
        if len(cols) < 3:
            continue
        names = _extract_backtick_names(cols[0])
        if not names:
            continue
        name = names[0]
        if name in variables:
            raise ProcessContractError(f"Duplicate variable row for {name}")
        variable_type = cols[1]
        description = cols[2]
        if not variable_type:
            raise ProcessContractError(f"Variable {name} is missing a type")
        if not description:
            raise ProcessContractError(f"Variable {name} is missing a description")
        variables[name] = {"description": description, "type": variable_type}
        order.append(name)

    if not variables:
        raise ProcessContractError("process doc must declare an exhaustive ## Variables table")
    return variables, order


def _extract_process_environment(lines: list[str]) -> list[str]:
    env_vars: list[str] = []
    seen: set[str] = set()
    in_section = False
    for line in lines:
        trimmed = line.strip()
        if trimmed.lower() == "### environment":
            in_section = True
            continue
        if not in_section:
            continue
        if trimmed.startswith("### ") or trimmed.startswith("## ") or trimmed == "---":
            break
        if not trimmed:
            continue

        entries = _extract_backtick_names(trimmed)
        if not entries:
            normalized = _normalize_declared_capability(trimmed)
            if normalized:
                entries = [normalized]
        for entry in entries:
            name = str(entry).strip()
            if not name or not _looks_like_env_var(name) or name in seen:
                continue
            seen.add(name)
            env_vars.append(name)
    return env_vars


def _variable_item(name: str, variables: dict[str, dict[str, str]]) -> dict[str, Any]:
    meta = variables.get(name)
    if meta is None:
        raise ProcessContractError(f"Variable {name} is referenced by steps but missing from ## Variables")
    variable_type = meta["type"]
    return {
        "name": name,
        "detail": "",
        "description": meta["description"],
        "type": variable_type,
        "is_resource": False,
    }


def _normalize_declared_capability(text: str) -> str:
    normalized = text.strip()
    if normalized.startswith("- "):
        normalized = normalized[2:].strip()
    if normalized.startswith("`") and normalized.endswith("`") and len(normalized) >= 2:
        normalized = normalized[1:-1].strip()
    return normalized


def _looks_like_env_var(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", name.strip()))


def _load_tool_registry() -> ToolRegistry | None:
    tool_dir = os.environ.get("TOOL_DIR")
    if tool_dir:
        tool_path = Path(tool_dir)
    else:
        tool_path = Path(__file__).resolve().parents[2] / "libs" / "tools"
    if not tool_path.exists():
        return None

    registry = ToolRegistry(tool_dir=str(tool_path))
    try:
        registry.load()
    except ValidationAppError as exc:
        raise ProcessContractError(f"Tool registry could not be loaded: {exc}") from exc
    return registry


def _validate_declared_tools(steps: list[dict[str, Any]]) -> None:
    registry = _load_tool_registry()
    if registry is None:
        return

    for step in steps:
        for tool_name in step.get("tools", []):
            try:
                registry.get(tool_name)
            except NotFoundAppError as exc:
                raise ProcessContractError(
                    f"Step {step['number']} declares unknown tool {tool_name!r}"
                ) from exc


def _parse_step_header(header: str, current_step_num: int) -> tuple[int, str]:
    body = header[len("### Step ") :]
    if " — " in body:
        num_raw, title = body.split(" — ", 1)
    elif " -- " in body:
        num_raw, title = body.split(" -- ", 1)
    else:
        current_step_num += 1
        return current_step_num, body.strip()
    return int(num_raw.strip()), title.strip()


def _extract_json_keys(content: str) -> list[str]:
    regex = re.compile(r'^ {0,2}"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:')
    keys: list[str] = []
    for line in content.splitlines():
        match = regex.match(line)
        if match:
            keys.append(match.group(1))
    return keys


def _extract_backtick_names(text: str) -> list[str]:
    names: list[str] = []
    remaining = text
    while True:
        open_idx = remaining.find("`")
        if open_idx < 0:
            return names
        close_idx = remaining.find("`", open_idx + 1)
        if close_idx < 0:
            return names
        name = remaining[open_idx + 1 : close_idx]
        if name:
            names.append(name)
        remaining = remaining[close_idx + 1 :]


def _extract_output_name(text: str) -> str:
    start = text.find("(")
    if start < 0:
        return "output"
    end = text.find(")", start + 1)
    if end < 0:
        return "output"
    return text[start + 1 : end]


def _strip_bold_label(text: str) -> str:
    work = text[2:] if text.startswith("**") else text
    if ":**" in work:
        _, rest = work.split(":**", 1)
        return rest.strip()
    if "**" in work:
        _, rest = work.split("**", 1)
        return rest.strip()
    return work.strip()
