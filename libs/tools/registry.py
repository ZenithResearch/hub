from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from libs.common.errors import NotFoundAppError, ValidationAppError

from .contracts import ToolManifest


@dataclass(frozen=True)
class RegisteredTool:
    manifest: ToolManifest
    manifest_path: Path


class ToolRegistry:
    """Deny-by-default tool allowlist loaded from manifests."""

    def __init__(self, *, tool_dir: str) -> None:
        self._tool_dir = Path(tool_dir)
        self._tools: dict[str, RegisteredTool] = {}

    def load(self) -> None:
        if not self._tool_dir.exists():
            raise ValidationAppError(f"TOOL_DIR does not exist: {self._tool_dir}")

        tools: dict[str, RegisteredTool] = {}
        for manifest_path in self._tool_dir.rglob("manifest.json"):
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            try:
                manifest = ToolManifest.model_validate(raw)
            except Exception as e:  # pragma: no cover
                raise ValidationAppError(
                    f"Invalid tool manifest: {manifest_path}", details={"error": str(e)}
                )

            if manifest.name in tools:
                raise ValidationAppError(
                    f"Duplicate tool name in registry: {manifest.name}",
                    details={"manifest_path": str(manifest_path)},
                )

            tools[manifest.name] = RegisteredTool(
                manifest=manifest, manifest_path=manifest_path
            )

        self._tools = tools

    def list_tools(self) -> list[ToolManifest]:
        return [t.manifest for t in self._tools.values()]

    def get(self, tool_name: str) -> ToolManifest:
        tool = self._tools.get(tool_name)
        if not tool:
            raise NotFoundAppError(f"Unknown tool: {tool_name}")
        return tool.manifest

