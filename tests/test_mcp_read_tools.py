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


def _prepare_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str
) -> str:
    import agents_mcp.server as server

    monkeypatch.setenv("BIZGUARD_CONTEXT_DB", str(tmp_path / "contexts.sqlite3"))
    monkeypatch.setattr(server, "_change_store", None)
    prepared = _call(
        "prepare_change",
        {
            "task": task,
            "repos": ["coupon-core"],
            "base_revisions": {
                "coupon-core": "fixture-coupon-core-base",
                "__index__": "phase3-fixture-v1",
            },
        },
    )
    return str(prepared["change_context_id"])


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


def test_request_approval_rejects_legacy_schema_only_args() -> None:
    with pytest.raises(ToolError):
        _call("request_approval", {"change_context_id": "ctx", "requested_by": "engineering", "reason": "review"})


def test_request_approval_persists_pending_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agents_mcp.server as server

    monkeypatch.setenv("BIZGUARD_APPROVAL_DB", str(tmp_path / "approvals.sqlite3"))
    monkeypatch.setattr(server, "_approval_store", None)
    context_id = _prepare_context(tmp_path, monkeypatch, "persist approval")
    created = _call(
        "request_approval",
        {
            "change_context_id": context_id,
            "policy_revision": "phase5",
            "decision_fingerprint": "a" * 64,
            "approvers": ["coupon_platform"],
            "required_cosigns": 1,
        },
    )
    assert created["state"] == "pending"
    assert created["change_context_id"] == context_id


def test_get_change_decision_attaches_approval_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agents_mcp.server as server

    monkeypatch.setenv("BIZGUARD_APPROVAL_DB", str(tmp_path / "approvals.sqlite3"))
    monkeypatch.setattr(server, "_approval_store", None)
    context_id = _prepare_context(tmp_path, monkeypatch, "dynamic mapper approval")
    diff_text = (ROOT / "bench" / "fixtures" / "phase5" / "dynamic-mapper.diff").read_text(encoding="utf-8")
    pending = _call(
        "get_change_decision",
        {"diff_text": diff_text, "change_context_id": context_id},
    )
    fingerprint = str(pending["decision_fingerprint"])
    _call(
        "request_approval",
        {
            "change_context_id": context_id,
            "policy_revision": "phase5",
            "decision_fingerprint": fingerprint,
            "approvers": ["coupon_platform"],
            "required_cosigns": 1,
        },
    )
    pending = _call(
        "get_change_decision",
        {"diff_text": diff_text, "change_context_id": context_id},
    )
    assert pending["decision"] == "REQUIRE_APPROVAL"
    assert pending["approval_state"] == "pending"

    monkeypatch.setenv("BIZGUARD_CALLER_IDENTITY", "coupon_platform")
    approved = _call(
        "request_approval",
        {
            "change_context_id": context_id,
            "policy_revision": "phase5",
            "decision_fingerprint": fingerprint,
            "action": "approve",
        },
    )
    assert approved["state"] == "approved"
    released = _call(
        "get_change_decision",
        {"diff_text": diff_text, "change_context_id": context_id},
    )
    assert released["decision"] == "ALLOW_WITH_TESTS"
    assert released["approval_state"] == "approved"


def test_approval_actor_is_taken_from_authenticated_server_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agents_mcp.server as server

    monkeypatch.setenv("BIZGUARD_APPROVAL_DB", str(tmp_path / "approvals.sqlite3"))
    monkeypatch.setenv("BIZGUARD_CALLER_IDENTITY", "engineering")
    monkeypatch.setattr(server, "_approval_store", None)
    context_id = _prepare_context(tmp_path, monkeypatch, "authenticated approval")
    _call(
        "request_approval",
        {
            "change_context_id": context_id,
            "policy_revision": "phase5",
            "decision_fingerprint": "a" * 64,
            "approvers": ["coupon_platform"],
        },
    )
    with pytest.raises(ToolError):
        _call(
            "request_approval",
            {
                "change_context_id": context_id,
                "policy_revision": "phase5",
                "decision_fingerprint": "a" * 64,
                "action": "approve",
                "actor": "coupon_platform",
            },
        )


def test_mcp_caller_cannot_self_assert_test_completion() -> None:
    diff_text = (ROOT / "sample/diffs/diff_normal_1.diff").read_text(encoding="utf-8")
    decision = _call("get_change_decision", {"diff_text": diff_text, "tests_passed": True})
    assert decision["decision"] == "ALLOW_WITH_TESTS"
    tool = next(
        item for item in asyncio.run(mcp.list_tools()) if item.name == "get_change_decision"
    )
    assert "tests_passed" not in tool.inputSchema["properties"]


def test_repository_root_cannot_escape_the_configured_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agents_mcp.server as server

    allowed = ROOT / "fixtures/java-microservices"
    monkeypatch.setenv("BIZGUARD_REPOSITORY_ROOT", str(allowed))
    monkeypatch.setattr(server, "_change_store", None)
    diff_text = (ROOT / "sample/diffs/diff_normal_1.diff").read_text(encoding="utf-8")
    with pytest.raises(ToolError, match="outside the configured workspace"):
        server.get_change_decision(diff_text, repository_root=str(tmp_path))
