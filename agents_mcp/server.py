"""FastMCP read adapters delegating to BizGuard's shared core services."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import ChangeDecision, EvaluationRequest
from bizguard.change.store import ChangeContextStore
from bizguard.context.compiler import ContextCompiler
from bizguard.decision import ChangeSafetyCard, evaluate_change
from bizguard.impact.service import ImpactService
from bizguard.knowledge.ingest import ingest_directory
from bizguard.knowledge.models import SearchRequest
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.knowledge.search import HybridSearch, LocalVectorAdapter
from bizguard.semantic.models import load_catalog
from bizguard.semantic.required_tests import select_required_tests
from bizguard.symbols.service import SymbolService
from bizguard.workflow.approval import ApprovalRequest, ApprovalService
from bizguard.workflow.store import SqliteApprovalStore


mcp = FastMCP("bizguard")
_ROOT = Path(__file__).parents[1]
_REPOSITORIES = _ROOT / "fixtures/java-microservices"
_CATALOG = _ROOT / "src/bizguard/semantic/catalog.yaml"

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

_approval_store: SqliteApprovalStore | None = None
_change_store: ChangeContextStore | None = None


def _approval_service() -> ApprovalService:
    global _approval_store
    if _approval_store is None:
        path = Path(os.environ.get("BIZGUARD_APPROVAL_DB", _ROOT / ".artifacts" / "approvals.sqlite3"))
        _approval_store = SqliteApprovalStore(path)
    return ApprovalService(store=_approval_store)


def _read_approval(change_context_id: str, policy_revision: str) -> ApprovalRequest | None:
    if _approval_store is None:
        return None
    payload = _approval_store.get_by_context(change_context_id, policy_revision)
    return ApprovalRequest.model_validate_json(payload) if payload is not None else None


def _configured_repository_root() -> Path:
    return Path(os.environ.get("BIZGUARD_REPOSITORY_ROOT", _REPOSITORIES)).resolve()


def _resolve_repository_root(requested: str | None = None) -> Path:
    allowed = _configured_repository_root()
    candidate = Path(requested).resolve() if requested else allowed
    try:
        candidate.relative_to(allowed)
    except ValueError as exc:
        raise ToolError("repository_root is outside the configured workspace") from exc
    return candidate


def _change_context_store() -> ChangeContextStore:
    global _change_store
    if _change_store is None:
        path = Path(os.environ.get("BIZGUARD_CONTEXT_DB", _ROOT / ".artifacts" / "contexts.sqlite3"))
        _change_store = ChangeContextStore(path)
    return _change_store


def _caller_roles() -> set[str]:
    """Return server-authenticated roles; resource URIs never supply their own roles."""
    return {
        role.strip()
        for role in os.environ.get("BIZGUARD_CALLER_ROLES", "engineering").split(",")
        if role.strip()
    }


def _caller_identity() -> str:
    """Return the deployment-authenticated principal, never a tool argument."""
    return os.environ.get("BIZGUARD_CALLER_IDENTITY", "engineering").strip()


def _compiler() -> ContextCompiler:
    return ContextCompiler(_configured_repository_root(), store=_change_context_store())


@mcp.tool(description="从任务、仓库和基线版本真实编译只读 Context Pack；没有副作用。", annotations=_READ_ONLY)
def prepare_change(
    task: str | None = None,
    repos: list[str] | None = None,
    base_revisions: dict[str, str] | None = None,
    principal: str = "engineering",
    token_budget: int = 2000,
    diff_text: str | None = None,
) -> dict[str, object]:
    """Compile task input; ``diff_text`` returns an explicitly marked legacy decision adapter."""
    if diff_text is not None:
        return {"legacy": True, "result": evaluate_change(diff_text).model_dump(mode="json")}
    if task is None or repos is None or base_revisions is None:
        raise ToolError("task, repos, and base_revisions are required for Context Pack compilation")
    return _compiler().compile(task, repos, base_revisions, principal, token_budget).model_dump(mode="json")


@mcp.tool(description="按 ACL、版本和 scope 检索团队知识；只读且没有副作用。", annotations=_READ_ONLY)
def search_team_knowledge(
    query: str, scope: str, revision: str, caller_roles: list[str], limit: int = 5
) -> dict[str, object]:
    """Search revision-pinned team knowledge under caller ACLs."""
    repository = KnowledgeRepository.memory()
    try:
        ingest_directory(_ROOT / "knowledge/published", repository)
        result = HybridSearch(repository, LocalVectorAdapter()).search(
            SearchRequest(query=query, scope=scope, revision=revision, caller_roles=caller_roles, limit=limit)
        )
        return result.model_dump(mode="json")
    finally:
        repository.close()


@mcp.tool(description="解释指定的已索引符号及其真实图证据；只读且没有副作用。", annotations=_READ_ONLY)
def explain_symbol(symbol: str, revision: str) -> dict[str, object]:
    """Return indexed symbol details and graph evidence."""
    return SymbolService(_configured_repository_root()).explain(symbol, revision).model_dump(mode="json")


@mcp.tool(description="基于真实图快照分析影响路径、未知边界和必需测试；只读且没有副作用。", annotations=_READ_ONLY)
def analyze_impact(changed_symbol: str, revision: str, capability: str = "coupon_redemption") -> dict[str, object]:
    """Analyze the impact path for an indexed symbol."""
    return ImpactService(_configured_repository_root()).analyze(
        changed_symbol, revision, capability
    ).model_dump(mode="json")


@mcp.tool(description="只读确定性 unified diff 校验：不写入文件、不调用外部服务。P5 将与聚合决策分叉。", annotations=_READ_ONLY)
def validate_patch(diff_text: str) -> ChangeSafetyCard:
    """Validate a unified diff with the deterministic policy checks."""
    return evaluate_change(diff_text)


@mcp.tool(description="按语义 catalog 选择必需测试；只读且没有副作用。", annotations=_READ_ONLY)
def get_required_tests(capability: str, policy_id: str) -> list[dict[str, object]]:
    """Return required tests for a capability and policy."""
    catalog = load_catalog(_CATALOG)
    return [item.model_dump() for item in select_required_tests(catalog, capability, policy_id)]


@mcp.tool(description="写入审批请求：持久化审批记录（显式副作用写工具）；仅在客户端批准该写工具时执行。", annotations=_WRITE)
def request_approval(
    change_context_id: str,
    policy_revision: str,
    decision_fingerprint: str,
    action: Literal["create", "approve", "reject", "add_evidence"] = "create",
    approvers: list[str] | None = None,
    required_cosigns: int = 1,
    evidence_refs: list[str] | None = None,
    reason: str | None = None,
    evidence: str | None = None,
) -> dict[str, object]:
    """Create or advance a persisted approval request."""
    service = _approval_service()
    if action == "create":
        if _change_context_store().get(change_context_id) is None:
            raise ToolError("change context unavailable")
        request = service.create(
            ApprovalRequest(
                change_context_id=change_context_id,
                policy_revision=policy_revision,
                decision_fingerprint=decision_fingerprint,
                approvers=tuple(approvers or []),
                required_cosigns=required_cosigns,
                requested_by=_caller_identity(),
                evidence_refs=list(evidence_refs or []),
            )
        )
        return request.model_dump(mode="json")
    existing = _read_approval(change_context_id, policy_revision)
    if existing is None:
        raise ToolError("approval request unavailable")
    if existing.decision_fingerprint != decision_fingerprint:
        raise ToolError("approval request fingerprint mismatch")
    actor = _caller_identity()
    if action == "approve":
        service.approve(existing, actor)
    elif action == "reject":
        if reason is None:
            raise ToolError("reason is required to reject")
        service.reject(existing, actor, reason)
    else:
        if evidence is None:
            raise ToolError("evidence is required")
        service.add_evidence(existing, evidence)
    return existing.model_dump(mode="json")


@mcp.tool(description="聚合确定性校验为四态决定、证据链、必需测试和审批人；只读且没有副作用。", annotations=_READ_ONLY)
def get_change_decision(
    diff_text: str,
    repository_root: str | None = None,
    change_context_id: str | None = None,
    policy_revision: str = "phase5",
) -> ChangeDecision:
    """Return a canonical decision; untrusted MCP callers cannot assert CI test success."""
    root = _resolve_repository_root(repository_root)
    if change_context_id is not None:
        if _change_context_store().get(change_context_id) is None:
            raise ToolError("change context unavailable")
        _approval_service()
    store = _approval_store
    return ChangeEvaluator(root, approval_store=store).evaluate(
        EvaluationRequest(
            diff_text=diff_text,
            repository_root=root,
            change_context_id=change_context_id,
            policy_revision=policy_revision,
        )
    )


@mcp.resource("bizguard://changes/{change_context_id}")
def change_resource(change_context_id: str) -> dict[str, object]:
    """Summarize a persisted change context without dumping full documents."""
    payload = _change_context_store().get(change_context_id)
    if payload is None:
        raise ToolError("resource unavailable")
    from bizguard.context.compiler import ContextPack

    context = ContextPack.model_validate_json(payload)
    return {
        "change_context_id": change_context_id,
        "summary": context.task,
        "revision": context.index_revision,
        "freshness": "stale" if context.stale else "current",
        "confidence": 1.0 if not context.unknowns else 0.5,
        "evidence_links": [str(item.get("id")) for item in context.evidence],
    }


@mcp.resource("bizguard://symbols/{symbol_id}")
def symbol_resource(symbol_id: str) -> dict[str, object]:
    """Summarize an indexed symbol and its graph evidence links."""
    from urllib.parse import unquote

    symbol = unquote(symbol_id)
    try:
        result = SymbolService(_configured_repository_root()).explain(
            symbol,
            "phase3-fixture-v1",
        )
    except ValueError:
        raise ToolError("resource unavailable") from None
    return {
        "id": symbol,
        "summary": result.label,
        "kind": result.kind,
        "revision": result.revision,
        "freshness": None,
        "confidence": 1.0,
        "evidence_links": result.evidence_uris,
    }


@mcp.resource("bizguard://capabilities/{capability_id}")
def capability_resource(capability_id: str) -> dict[str, object]:
    """Summarize a business capability and its owning team."""
    catalog = load_catalog(_CATALOG)
    try:
        capability = catalog.capability(capability_id)
    except KeyError:
        raise ToolError("resource unavailable") from None
    return {
        "id": capability.id,
        "summary": capability.name,
        "revision": catalog.revision,
        "freshness": None,
        "confidence": 1.0,
        "evidence_links": [],
        "owner": capability.owner,
    }


@mcp.resource("bizguard://policies/{policy_id}")
def policy_resource(policy_id: str) -> dict[str, object]:
    """Summarize a registered policy without dumping its full registry."""
    from bizguard.policy.registry import load_registry

    registry = load_registry(_ROOT / "policy" / "phase5-registry.yaml")
    policy = next((item for item in registry if item.id == policy_id), None)
    if policy is None:
        raise ToolError("resource unavailable")
    return {
        "id": policy.id,
        "summary": policy.remediation,
        "revision": "phase5",
        "freshness": None,
        "confidence": 1.0,
        "evidence_links": [],
        "severity": policy.severity,
        "mode": policy.mode.value,
    }


@mcp.resource("bizguard://evidence/{evidence_id}")
def evidence_resource(evidence_id: str) -> dict[str, object]:
    """Summarize one knowledge document without dumping its full content."""
    repository = KnowledgeRepository.memory()
    try:
        ingest_directory(_ROOT / "knowledge/published", repository)
        entry = next((item for item in repository.all() if item.id == evidence_id), None)
        if entry is None or not set(entry.acl).intersection(_caller_roles()):
            raise ToolError("resource unavailable")
        return {
            "id": entry.id,
            "summary": entry.title,
            "revision": entry.source_revision,
            "freshness": entry.expires_at.isoformat() if entry.expires_at else None,
            "confidence": entry.confidence,
            "evidence_links": [entry.evidence_uri],
        }
    finally:
        repository.close()


if __name__ == "__main__":
    mcp.run()
