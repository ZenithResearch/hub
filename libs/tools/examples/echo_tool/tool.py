from __future__ import annotations

from typing import Any


def run(tool_input: dict[str, Any], *, request_id: str = "") -> dict[str, Any]:
    """Echo input payload back to the caller.

    This tool exists purely to validate the tool contract + sandbox plumbing.
    """
    return {"request_id": request_id, "echo": tool_input}

