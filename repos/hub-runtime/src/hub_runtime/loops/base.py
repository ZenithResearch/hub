from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Any

from hub_runtime.core.context import RuntimeContext

ToolRegistry = dict[str, Callable[..., Any]]


class BaseAgentLoop(ABC):
    """Strategy interface for agent execution loops."""

    @abstractmethod
    def run(
        self,
        context: RuntimeContext,
        tools: ToolRegistry,
        env_vars: Mapping[str, str],
    ) -> str:
        """Run the loop and return the final assistant output."""
