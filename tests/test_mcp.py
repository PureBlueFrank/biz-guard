"""MCP adapter tests for the shared BizGuard decision pipeline."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from mcp.server.fastmcp.exceptions import ToolError
import pytest

from agents_mcp.server import mcp
from bizguard.decision import evaluate_change


PROJECT_ROOT = Path(__file__).parent.parent


def _call_tool(name: str, arguments: dict[str, object]) -> object:
    return asyncio.run(mcp.call_tool(name, arguments))


def _structured_card(result: object) -> dict[str, object]:
    """Extract FastMCP's structured result from a direct protocol tool call."""
    assert isinstance(result, tuple)
    assert len(result) == 2
    card = result[1]
    assert isinstance(card, dict)
    return card


def test_mcp_tools_are_registered_with_json_schemas() -> None:
    """Phase 4 exposes the complete typed MCP surface, including the schema-only write gate."""
    tools = asyncio.run(mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert set(by_name) == {
        "prepare_change", "search_team_knowledge", "explain_symbol", "analyze_impact",
        "validate_patch", "get_required_tests", "request_approval", "get_change_decision",
    }
    for tool in by_name.values():
        assert tool.inputSchema["properties"]
        assert tool.outputSchema is not None
        assert "只读" in (tool.description or "") or tool.name == "request_approval"
    assert by_name["validate_patch"].inputSchema["properties"]["diff_text"]["type"] == "string"
    assert by_name["get_change_decision"].inputSchema["properties"]["diff_text"]["type"] == "string"


def test_mcp_tool_matches_shared_decision() -> None:
    """The registered FastMCP tool reaches the shared core without policy logic of its own."""
    diff_text = (PROJECT_ROOT / "sample/diffs/diff_violation_1.diff").read_text(encoding="utf-8")

    result = _call_tool("validate_patch", {"diff_text": diff_text})

    assert _structured_card(result) == evaluate_change(diff_text).model_dump(mode="json")


def test_cli_and_mcp_return_the_same_card() -> None:
    """CLI and MCP retain identical decision, findings, and evidence for one input."""
    diff_path = PROJECT_ROOT / "sample/diffs/diff_violation_1.diff"
    environment = os.environ | {"PYTHONPATH": str(PROJECT_ROOT / "src")}
    cli = subprocess.run(
        [sys.executable, "-m", "bizguard.cli", "check", "--diff", str(diff_path)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    mcp_result = _call_tool("prepare_change", {"diff_text": diff_path.read_text(encoding="utf-8")})

    assert cli.returncode == 1
    response = _structured_card(mcp_result)
    assert response["legacy"] is True
    assert json.loads(cli.stdout) == response["result"]


def test_mcp_rejects_non_string_diff_text() -> None:
    """FastMCP's generated JSON Schema rejects malformed tool arguments."""
    with pytest.raises(ToolError, match="Input should be a valid string"):
        _call_tool("validate_patch", {"diff_text": 42})
