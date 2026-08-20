"""MCP read adapters must delegate to the shared services, not literals."""

import asyncio
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from agents_mcp.server import mcp
from bizguard.impact.service import ImpactService
from bizguard.knowledge.ingest import ingest_directory
from bizguard.knowledge.models import SearchRequest
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.knowledge.search import HybridSearch, LocalVectorAdapter
from bizguard.semantic.models import load_catalog
from bizguard.semantic.required_tests import select_required_tests
from bizguard.symbols.service import SymbolService


ROOT = Path(__file__).parent.parent


def _call(name: str, arguments: dict[str, object]) -> dict[str, object]:
    result = asyncio.run(mcp.call_tool(name, arguments))
    assert isinstance(result, tuple) and isinstance(result[1], dict)
    return result[1]


def test_all_eight_tools_have_nonempty_json_schema() -> None:
    tools = asyncio.run(mcp.list_tools())
    assert len(tools) == 8
    assert all(tool.inputSchema["properties"] for tool in tools)


def test_prepare_change_delegates_to_context_compiler() -> None:
    arguments: dict[str, object] = {"task": "status", "repos": ["coupon-core"], "base_revisions": {"coupon-core": "fixture-coupon-core-base", "__index__": "phase3-fixture-v1"}}
    assert _call("prepare_change", arguments)["task"] == "status"


def test_search_tool_delegates_to_hybrid_search() -> None:
    arguments: dict[str, object] = {"query": "status", "scope": "coupon_redemption", "revision": "semantic-seed-v1", "caller_roles": ["engineering"]}
    repository = KnowledgeRepository.memory()
    try:
        ingest_directory(ROOT / "knowledge/published", repository)
        expected = HybridSearch(repository, LocalVectorAdapter()).search(SearchRequest(query="status", scope="coupon_redemption", revision="semantic-seed-v1", caller_roles=["engineering"])).model_dump(mode="json")
    finally:
        repository.close()
    assert _call("search_team_knowledge", arguments) == expected


def test_explain_symbol_delegates_to_symbol_service() -> None:
    arguments: dict[str, object] = {"symbol": "db://coupon-core/coupon_redemption#status", "revision": "phase3-fixture-v1"}
    expected = SymbolService(ROOT / "fixtures/java-microservices").explain("db://coupon-core/coupon_redemption#status", "phase3-fixture-v1").model_dump(mode="json")
    assert _call("explain_symbol", arguments) == expected


def test_analyze_impact_delegates_to_impact_service() -> None:
    arguments: dict[str, object] = {"changed_symbol": "db://coupon-core/coupon_redemption#status", "revision": "phase3-fixture-v1"}
    expected = ImpactService(ROOT / "fixtures/java-microservices").analyze("db://coupon-core/coupon_redemption#status", "phase3-fixture-v1").model_dump(mode="json")
    assert _call("analyze_impact", arguments) == expected


def test_required_tests_delegates_to_semantic_catalog() -> None:
    arguments: dict[str, object] = {"capability": "coupon_redemption", "policy_id": "coupon-redemption-aggregate-idempotency-key"}
    catalog = load_catalog(ROOT / "src/bizguard/semantic/catalog.yaml")
    expected = [item.model_dump() for item in select_required_tests(catalog, "coupon_redemption", "coupon-redemption-aggregate-idempotency-key")]
    assert _call("get_required_tests", arguments) == {"result": expected}


def test_schema_only_approval_never_writes() -> None:
    with pytest.raises(ToolError, match="schema-only"):
        _call("request_approval", {"change_context_id": "ctx", "requested_by": "engineering", "reason": "review"})
