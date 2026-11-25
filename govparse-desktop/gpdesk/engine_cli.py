"""One MCP stdio session against the govparse server, as a killable child process.

usage: python -m gpdesk.engine_cli <calls.json>
calls.json = {"engine_python":..., "engine_src":..., "data_root":..., "calls": [[name, args], ...]}
Prints one JSON object: {"tools": [...], "results": [{"name", "text", "is_error"}]}
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


async def _main(spec: dict) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ, PYTHONPATH=spec["engine_src"], PYTHONUTF8="1",
               GOVPARSE_DATA=spec["data_root"])
    params = StdioServerParameters(command=spec["engine_python"],
                                   args=["-m", "govparse.mcp_server"], env=env)
    out: dict = {"tools": [], "results": []}
    with open(os.devnull, "w") as errlog:
        async with stdio_client(params, errlog=errlog) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                out["tools"] = [t.name for t in (await s.list_tools()).tools]
                for name, args in spec["calls"]:
                    res = await s.call_tool(name, args)
                    text = "".join(getattr(c, "text", "") for c in res.content)
                    out["results"].append({"name": name, "text": text, "is_error": bool(res.isError)})
    return out


def main() -> int:
    spec = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    result = asyncio.run(_main(spec))
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
