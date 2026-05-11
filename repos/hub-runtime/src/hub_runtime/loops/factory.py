from __future__ import annotations

from hub_runtime.loops.base import BaseAgentLoop
from hub_runtime.loops.hermes import HermesAgentLoop


def create_loop(loop_type: str) -> BaseAgentLoop:
    normalized_loop_type = loop_type.strip().lower()
    if normalized_loop_type in {"", "hermes", "hermes_worker"}:
        return HermesAgentLoop()
    raise ValueError(f"Unsupported LOOP_TYPE: {loop_type!r}")
