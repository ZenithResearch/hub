from __future__ import annotations

import importlib.util
import inspect
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any


DEFAULT_TOOLS_DIR = Path("/hub_tools")
ToolRegistry = dict[str, Callable[..., Any]]


def tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for mounted functions that should be exposed as tools."""

    setattr(fn, "__hub_tool__", True)
    return fn


def load_tools(tools_dir: Path | str = DEFAULT_TOOLS_DIR) -> ToolRegistry:
    """Import mounted Python files and register explicitly declared tools.

    Only register functions decorated with @tool or prefixed tool_. All other
    callables in the file are treated as private helpers and ignored.
    """

    registry: ToolRegistry = {}
    root = Path(tools_dir)
    if not root.exists():
        return registry

    root_string = str(root)
    if root_string not in sys.path:
        sys.path.insert(0, root_string)

    for script_path in sorted(root.rglob("*.py")):
        if script_path.name == "__init__.py":
            continue
        module = _import_tool_module(script_path, root)
        for name, value in inspect.getmembers(module, inspect.isfunction):
            if value.__module__ != module.__name__:
                continue
            if not _is_tool_callable(name, value):
                continue
            registry[name] = value
    return registry


def _import_tool_module(script_path: Path, root: Path) -> ModuleType:
    relative = script_path.relative_to(root).with_suffix("")
    module_name = "hub_mounted_tools_" + "_".join(_sanitize_module_part(part) for part in relative.parts)
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import tool module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    module.tool = tool
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _sanitize_module_part(value: str) -> str:
    sanitized = re.sub(r"\W+", "_", value)
    if sanitized and sanitized[0].isdigit():
        return f"_{sanitized}"
    return sanitized or "tool"


def _is_tool_callable(name: str, value: Callable[..., Any]) -> bool:
    return bool(getattr(value, "__hub_tool__", False)) or name.startswith("tool_")
