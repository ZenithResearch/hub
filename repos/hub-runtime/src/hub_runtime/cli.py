from __future__ import annotations

import json
import os
import sys

from hub_runtime.core.config import RuntimeConfig
from hub_runtime.core.context import load_context
from hub_runtime.loops.factory import create_loop
from hub_runtime.tools.loader import load_tools


def main() -> None:
    env_vars = dict(os.environ)
    config = RuntimeConfig.from_env(env_vars)
    context = load_context()
    tools = load_tools()
    loop = create_loop(config.loop_type)
    result = loop.run(context=context, tools=tools, env_vars=env_vars)

    sys.stdout.write(json.dumps({"status": "complete", "output": result}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
