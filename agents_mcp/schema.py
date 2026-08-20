"""The sole export entrypoint for BizGuard MCP tool schemas."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .server import mcp


def export_schema() -> dict[str, object]:
    """Return the registered MCP tool schemas as JSON-compatible data."""
    tools = asyncio.run(mcp.list_tools())
    return {"tools": [tool.model_dump(by_alias=True, mode="json") for tool in tools]}


def main() -> None:
    """Export MCP tool schemas from command-line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(export_schema(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
