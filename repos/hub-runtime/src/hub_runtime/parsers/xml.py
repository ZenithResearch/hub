from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


TOOL_CALL_PATTERN = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE)
TOOL_RESPONSE_PATTERN = re.compile(
    r"<tool_response>\s*(.*?)\s*</tool_response>",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class HermesToolCall:
    name: str
    arguments: dict[str, Any]
    raw: str


def parse_tool_calls(text: str) -> list[HermesToolCall]:
    """Parse Hermes XML tool calls containing FunctionCall JSON payloads."""

    calls: list[HermesToolCall] = []
    for match in TOOL_CALL_PATTERN.finditer(text or ""):
        raw_payload = match.group(1).strip()
        payload = _loads_tool_json(raw_payload)
        name = payload.get("name")
        arguments = payload.get("arguments", {})
        if not isinstance(name, str) or not name:
            raise ValueError(f"Hermes tool call is missing a valid name: {raw_payload}")
        if not isinstance(arguments, dict):
            raise ValueError(f"Hermes tool call arguments must be an object: {raw_payload}")
        calls.append(HermesToolCall(name=name, arguments=arguments, raw=raw_payload))
    return calls


def has_tool_response(text: str) -> bool:
    return bool(TOOL_RESPONSE_PATTERN.search(text or ""))


def format_tool_response(name: str, content: Any) -> str:
    payload = {
        "name": name,
        "content": _stringify_content(content),
    }
    return f"<tool_response>\n{json.dumps(payload, ensure_ascii=False)}\n</tool_response>"


def strip_tool_calls(text: str) -> str:
    return TOOL_CALL_PATTERN.sub("", text or "").strip()


def _loads_tool_json(raw_payload: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        candidate = _extract_json_object(raw_payload)
        if candidate is None:
            raise ValueError(f"Invalid Hermes tool call JSON: {raw_payload}") from exc
        payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise ValueError(f"Hermes tool call payload must be an object: {raw_payload}")
    return payload


def _extract_json_object(raw_payload: str) -> str | None:
    start = raw_payload.find("{")
    end = raw_payload.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return raw_payload[start : end + 1]


def _stringify_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return repr(value)
