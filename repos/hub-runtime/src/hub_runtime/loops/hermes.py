from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping
from typing import Any, get_args, get_origin

from hub_runtime.core.config import RuntimeConfig
from hub_runtime.core.context import RuntimeContext
from hub_runtime.core.inference import create_inference_provider
from hub_runtime.loops.base import BaseAgentLoop, ToolRegistry
from hub_runtime.parsers.xml import format_tool_response, parse_tool_calls, strip_tool_calls


FUNCTION_CALL_SCHEMA = {
    "properties": {
        "arguments": {"title": "Arguments", "type": "object"},
        "name": {"title": "Name", "type": "string"},
    },
    "required": ["arguments", "name"],
    "title": "FunctionCall",
    "type": "object",
}


class HermesAgentLoop(BaseAgentLoop):
    """Hermes XML tool-calling loop backed by an OpenAI-compatible provider."""

    def run(
        self,
        context: RuntimeContext,
        tools: ToolRegistry,
        env_vars: Mapping[str, str],
    ) -> str:
        config = RuntimeConfig.from_env(env_vars)
        messages = self._initial_messages(context, tools)

        with create_inference_provider(config) as inference:
            for _ in range(config.max_iterations):
                assistant_content = inference.complete(messages).content
                tool_calls = parse_tool_calls(assistant_content)
                messages.append({"role": "assistant", "content": assistant_content})

                if not tool_calls:
                    return strip_tool_calls(assistant_content)

                for tool_call in tool_calls:
                    result = self._execute_tool(tool_call.name, tool_call.arguments, tools)
                    messages.append(
                        {
                            "role": "tool",
                            "name": tool_call.name,
                            "content": format_tool_response(tool_call.name, result),
                        }
                    )

        return "Maximum tool-calling iterations reached before a final response was produced."

    def _initial_messages(
        self,
        context: RuntimeContext,
        tools: ToolRegistry,
    ) -> list[dict[str, Any]]:
        system_prompt = "\n\n".join(
            part
            for part in [
                context.soul.strip(),
                self._tool_instruction_prompt(tools),
            ]
            if part
        )
        user_prompt = context.as_user_prompt() or "Continue."
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _tool_instruction_prompt(self, tools: ToolRegistry) -> str:
        tool_specs = [_tool_spec(name, fn) for name, fn in sorted(tools.items())]
        tools_payload = "\n".join(json.dumps(spec, ensure_ascii=False) for spec in tool_specs)
        return (
            "You are a function calling AI model. You are provided with function signatures within "
            "<tools></tools> XML tags. You may call one or more functions to assist with the user "
            "query. Don't make assumptions about what values to plug into functions.\n"
            f"Here are the available tools: <tools>\n{tools_payload}\n</tools>\n"
            "Use the following JSON schema for each tool call you will make: "
            f"{json.dumps(FUNCTION_CALL_SCHEMA, ensure_ascii=False)}\n"
            "For each function call return a json object with function name and arguments within "
            "<tool_call></tool_call> XML tags as follows:\n"
            "<tool_call>\n"
            '{"name": "tool_name", "arguments": {"arg": "value"}}\n'
            "</tool_call>\n\n"
            "Tool results are returned to you in this format:\n"
            "<tool_response>\n"
            '{"name": "tool_name", "content": "result text"}\n'
            "</tool_response>"
        )

    def _execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        tools: ToolRegistry,
    ) -> dict[str, Any]:
        tool = tools.get(name)
        if tool is None:
            return {
                "ok": False,
                "error": f"Tool {name!r} is not available.",
                "available_tools": sorted(tools),
            }

        try:
            value = tool(**arguments)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        return {"ok": True, "result": _json_safe(value)}


def _tool_spec(name: str, tool: Callable[..., Any]) -> dict[str, Any]:
    signature = inspect.signature(tool)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for parameter_name, parameter in signature.parameters.items():
        if parameter.kind in {
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        }:
            continue
        properties[parameter_name] = _annotation_to_schema(parameter.annotation)
        if parameter.default is inspect.Parameter.empty:
            required.append(parameter_name)

    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters_schema["required"] = required

    description = inspect.getdoc(tool) or f"Call the {name} tool."
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters_schema,
        },
    }


def _annotation_to_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation in {dict, dict[str, Any]}:
        return {"type": "object"}
    if annotation in {list, list[Any]}:
        return {"type": "array"}

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {list, tuple, set}:
        item_schema = _annotation_to_schema(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item_schema}
    if origin is dict:
        return {"type": "object"}
    if origin is None:
        return {"type": "string"}

    non_none_args = [arg for arg in args if arg is not type(None)]
    if len(non_none_args) == 1:
        return _annotation_to_schema(non_none_args[0])
    return {"type": "string"}


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except TypeError:
        return repr(value)
    return value
