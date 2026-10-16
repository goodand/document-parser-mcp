"""Actual stdio C3 smoke against an existing source-lineaged corpus.

Author: chatgpt_gpt6. Reports can contain local paths; do not commit document outputs.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--document", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    env = dict(os.environ, GOVPARSE_DATA=str(args.corpus.resolve()), PYTHONUTF8="1")
    server = StdioServerParameters(command=sys.executable, args=["-m", "govparse.mcp_server"], env=env)
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.call_tool("get_best_available", {
                "doc_name": args.document, "output_kind": "rag"})
            result = json.loads("".join(item.text for item in response.content if hasattr(item, "text")))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["route"] == "c3", result
    assert result.get("artifacts_ready") is True, result
    assert result["complete"] is False and result["status"] == "held", result
    core = json.loads(Path(result["c3_document"]).read_text(encoding="utf-8"))
    core.pop("combination_annotations")
    baseline = json.loads((Path(result["c3_document"]).parent / "document.json").read_text(encoding="utf-8"))
    assert core == baseline
    assert Path(result["rag_path"]).is_file()
    print(json.dumps({"counts": result["counts"], "phases": result["phases"], "core_unchanged": True}))


if __name__ == "__main__":
    asyncio.run(main())
