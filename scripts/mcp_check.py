"""Check the dbt MCP server (scripts/dbt-mcp.sh): it starts, and exposes only read-only tools.

    make mcp-check                                   # list the tools, fail on any write tool
    make mcp-check ARGS="--lineage model.wanderlake.fct_exchange_rate --node city_overview"

Runs in dbt-mcp's own environment (uvx --from dbt-mcp), which ships the MCP client library.
The optional calls use the same tools Claude Code uses (get_lineage_dev, get_node_details_dev).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READ_ONLY_TOOLS = {"list", "parse", "compile", "show", "get_lineage_dev", "get_node_details_dev"}
WRITE_TOOLS = {"build", "run", "test", "clone", "docs", "execute_sql", "text_to_sql"}


async def check(lineage: str | None, node: str | None, depth: int, types: list[str]) -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(command="bash", args=[str(ROOT / "scripts/dbt-mcp.sh")])
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = {t.name for t in (await session.list_tools()).tools}
        print("tools:", ", ".join(sorted(tools)))
        exposed = tools & WRITE_TOOLS
        unexpected = tools - READ_ONLY_TOOLS
        if exposed or unexpected:
            print(f"FAIL: tools that are not read-only: {sorted(exposed | unexpected)}")
            return 1
        print("OK: only read-only tools are exposed")
        for name, args in (
            (
                "get_lineage_dev",
                {"unique_id": lineage, "depth": depth, "types": types} if lineage else None,
            ),
            ("get_node_details_dev", {"node_id": node} if node else None),
        ):
            if args is None:
                continue
            result = await session.call_tool(name, args)
            print(f"\n== {name} {json.dumps(args)}")
            for part in result.content:
                print(getattr(part, "text", part))
            if result.isError:
                return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lineage", help="unique_id, e.g. model.wanderlake.fct_exchange_rate")
    parser.add_argument("--node", help="model name or unique_id for get_node_details_dev")
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument(
        "--types", default="Model,Source,Exposure", help="lineage resource types (no tests)"
    )
    args = parser.parse_args(argv)
    return asyncio.run(check(args.lineage, args.node, args.depth, args.types.split(",")))


if __name__ == "__main__":
    sys.exit(main())
