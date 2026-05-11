from __future__ import annotations

from typing import Any, Protocol

from .contracts import ToolResult


class ToolExecutor(Protocol):
    def invoke_tool(self, tool_name: str, input: dict[str, Any], request_id: str) -> ToolResult: ...

