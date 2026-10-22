"""Validation and readiness for the frozen legacy contract, not a new runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RESOURCE_ROOT = Path(__file__).resolve().parents[2] / "base/ops/processes/resources"


def errors(contract: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(contract, dict):
        return ["missing or malformed frozen contract"]
    steps, variables = contract.get("steps"), contract.get("variables")
    if not isinstance(steps, list) or not steps or not isinstance(variables, dict):
        return ["contract must contain steps and typed variables"]
    if any(not isinstance(step, dict) for step in steps):
        return ["malformed step"]
    if any(
        not isinstance(name, str) or not isinstance(meta, dict) for name, meta in variables.items()
    ):
        return ["malformed variable definition"]
    ids = [step.get("step_id") for step in steps]
    numbers = [step.get("number") for step in steps]
    if numbers != list(range(1, len(steps) + 1)) or ids != [f"step_{n}" for n in numbers]:
        problems.append("legacy step identities must be unique, positive and contiguous")
    for name, meta in variables.items():
        typ = str(meta.get("type", "")) if isinstance(meta, dict) else ""
        if not (
            typ.lower() in {"string", "number", "integer", "boolean", "array", "object"}
            or (typ.lower().startswith("string (") and typ.endswith(")"))
        ):
            problems.append(f"unsupported variable type: {name}")
    producers: dict[str, int] = {}
    expected_bindings: dict[tuple[int, int], set[str]] = {}
    for index, step in enumerate(steps):
        outputs = step.get("output_variables")
        inputs = step.get("input_items")
        if not isinstance(outputs, list) or not isinstance(inputs, list):
            problems.append(f"malformed IO for {ids[index]}")
            continue
        if any(not isinstance(name, str) for name in outputs):
            problems.append(f"malformed output identity for {ids[index]}")
            continue
        if len(outputs) != len(set(outputs)):
            problems.append(f"duplicate output for {ids[index]}")
        for name in outputs:
            if name not in variables or name in producers:
                problems.append(f"unknown or multiply produced output: {name}")
            producers[name] = index
        for item in inputs:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or item.get("name") not in variables
            ):
                problems.append(f"unknown input for {ids[index]}")
            elif item.get("type") != variables[item["name"]].get("type"):
                problems.append(f"input type mismatch for {ids[index]}:{item['name']}")
        typed_outputs = step.get("outputs")
        if not isinstance(typed_outputs, list):
            problems.append(f"malformed output types for {ids[index]}")
            continue
        if [
            item.get("name") if isinstance(item, dict) else None for item in typed_outputs
        ] != outputs:
            problems.append(f"output type definitions do not match outputs for {ids[index]}")
        for item in typed_outputs:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or item.get("name") not in variables
                or item.get("type") != variables[item["name"]].get("type")
            ):
                problems.append(f"output type mismatch for {ids[index]}")
    if problems:
        return list(dict.fromkeys(problems))
    for index, step in enumerate(steps):
        for item in step.get("input_items", []):
            if isinstance(item, dict) and item.get("name") in producers:
                expected_bindings.setdefault((producers[item["name"]], index), set()).add(
                    item["name"]
                )
    edges = contract.get("dag_edges")
    actual_edges: set[tuple[int, int]] = set()
    if not isinstance(edges, list):
        problems.append("missing compiled DAG")
    else:
        for edge in edges:
            if not isinstance(edge, dict):
                problems.append("malformed DAG edge")
                continue
            start, end = edge.get("from"), edge.get("to")
            if (
                type(start) is not int
                or type(end) is not int
                or not (0 <= start < len(steps) and 0 <= end < len(steps))
            ):
                problems.append("DAG edge references a missing step")
                continue
            if start >= end:
                problems.append("legacy DAG has a self, backward or cyclic dependency")
            if (start, end) in actual_edges:
                problems.append("duplicate compiled DAG edge")
            actual_edges.add((start, end))
            bindings = edge.get("variables")
            if not isinstance(bindings, list) or any(
                not isinstance(name, str) for name in bindings
            ):
                problems.append("malformed DAG variable bindings")
                continue
            if len(bindings) != len(set(bindings)):
                problems.append("duplicate DAG variable binding")
            if set(bindings) != expected_bindings.get((start, end), set()):
                problems.append("compiled DAG variable bindings do not match typed IO")
            for name in bindings:
                if producers.get(name) != start or name not in [
                    x.get("name") for x in steps[end].get("input_items", []) if isinstance(x, dict)
                ]:
                    problems.append("DAG variable binding does not match typed IO")
    if not set(expected_bindings).issubset(actual_edges):
        problems.append("compiled DAG is missing an IO dependency")
    declared_producers = contract.get("producer_map", {})
    if declared_producers != {key: value + 1 for key, value in producers.items()}:
        problems.append("producer map does not match frozen steps")
    expected_consumers = {name: [] for name in variables}
    for index, step in enumerate(steps):
        for item in step["input_items"]:
            expected_consumers[item["name"]].append(index + 1)
    if contract.get("consumer_map") != expected_consumers:
        problems.append("consumer map does not match frozen steps")
    slots = contract.get("slot_names")
    if (
        not isinstance(slots, list)
        or any(not isinstance(name, str) for name in slots)
        or len(slots) != len(variables)
        or set(slots) != set(variables)
    ):
        problems.append("slot map does not match frozen variable definitions")
    expected_roots = set(variables) - set(producers)
    roots = contract.get("root_inputs")
    if (
        not isinstance(roots, list)
        or any(not isinstance(name, str) for name in roots)
        or set(roots) != expected_roots
    ):
        problems.append("root input map does not match frozen steps")
    return list(dict.fromkeys(problems))


def state_errors(contract: Any, steps: list[dict[str, Any]]) -> list[str]:
    """Stored rows must cover the frozen graph exactly; never infer missing work."""
    problems = errors(contract)
    if problems:
        return problems
    actual = [(step.get("step_id"), step.get("idx")) for step in steps]
    expected = [(step["step_id"], index) for index, step in enumerate(contract["steps"])]
    if actual != expected:
        problems.append("stored step identities do not match the frozen contract")
    return problems


def load_profile(profile_id: str) -> dict[str, Any]:
    if profile_id != "personal-review-v1":
        raise ValueError("unsupported legacy execution profile")
    return json.loads((RESOURCE_ROOT / f"{profile_id}.json").read_text())


def profile_for(contract: dict[str, Any]) -> dict[str, Any]:
    """Use a frozen embedded resource or an explicit hash-pinned v1 adapter."""
    embedded = contract.get("legacy_execution")
    resource = load_profile("personal-review-v1")
    if embedded is not None:
        if (
            not isinstance(embedded, dict)
            or embedded.get("profile_id") != resource["profile_id"]
            or type(embedded.get("version")) is not int
            or embedded.get("version") != resource["version"]
            or embedded.get("handlers") != resource["handlers"]
            or embedded.get("dependencies") != resource["dependencies"]
        ):
            raise ValueError("unsupported or altered legacy handler resource")
        return embedded
    if contract.get("process_hash") not in resource["legacy_source_hashes"]:
        raise ValueError("legacy snapshot has no approved handler resource")
    return resource


def execution_errors(contract: Any) -> list[str]:
    problems = errors(contract)
    if problems:
        return problems
    try:
        profile = profile_for(contract)
        if set(profile["handlers"]) != {s["step_id"] for s in contract["steps"]}:
            problems.append("handler resource does not match frozen step identities")
        for step in contract["steps"]:
            spec = profile["handlers"][step["step_id"]]
            if set(step["output_variables"]) != set(spec["outputs"]):
                problems.append(f"handler output contract mismatch: {step['step_id']}")
        known = {s["step_id"] for s in contract["steps"]}
        for step_id, dependencies in profile["dependencies"].items():
            if step_id not in known or any(dependency not in known for dependency in dependencies):
                problems.append("handler resource references a missing dependency")
    except (ValueError, KeyError, TypeError) as exc:
        problems.append(str(exc))
    return problems


def runnable(
    contract: dict[str, Any],
    steps: list[dict[str, Any]],
    values: dict[str, Any],
    *,
    profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """All authority comes from compiled dependencies and populated typed inputs."""
    if state_errors(contract, steps):
        return []
    by_id = {step["step_id"]: step for step in steps}
    dependencies = {step["step_id"]: set() for step in contract["steps"]}
    for edge in contract["dag_edges"]:
        dependencies[contract["steps"][edge["to"]]["step_id"]].add(
            contract["steps"][edge["from"]]["step_id"]
        )
    for step_id, required in (profile or {}).get("dependencies", {}).items():
        dependencies[step_id].update(required)
    result = []
    for spec in contract["steps"]:
        step = by_id.get(spec["step_id"])
        if step is None or step["status"] not in {"PENDING", "READY"}:
            continue
        if any(
            by_id.get(dep, {}).get("status") != "COMPLETED" for dep in dependencies[spec["step_id"]]
        ):
            continue
        if all(values.get(item["name"]) is not None for item in spec["input_items"]):
            result.append(step)
    return result
