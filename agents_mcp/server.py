"""FastMCP read adapters delegating to BizGuard's shared core services."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

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


mcp = FastMCP("bizguard")
_ROOT = Path(__file__).parents[1]
_REPOSITORIES = _ROOT / "fixtures/java-microservices"
_CATALOG = _ROOT / "src/bizguard/semantic/catalog.yaml"


def _compiler() -> ContextCompiler:
    return ContextCompiler(_REPOSITORIES)


@mcp.tool(description="从任务、仓库和基线版本真实编译只读 Context Pack；没有副作用。")
def prepare_change(
    task: str | None = None,
    repos: list[str] | None = None,
    base_revisions: dict[str, str] | None = None,
    principal: str = "engineering",
    token_budget: int = 2000,
    diff_text: str | None = None,
) -> dict[str, object]:
    """Compile task input; ``diff_text`` remains a legacy compatibility adapter."""
    if diff_text is not None:
        return evaluate_change(diff_text).model_dump(mode="json")
    if task is None or repos is None or base_revisions is None:
        raise ToolError("task, repos, and base_revisions are required for Context Pack compilation")
    return _compiler().compile(task, repos, base_revisions, principal, token_budget).model_dump(mode="json")


@mcp.tool(description="按 ACL、版本和 scope 检索团队知识；只读且没有副作用。")
def search_team_knowledge(
    query: str, scope: str, revision: str, caller_roles: list[str], limit: int = 5
) -> dict[str, object]:
    repository = KnowledgeRepository.memory()
    try:
        ingest_directory(_ROOT / "knowledge/published", repository)
        result = HybridSearch(repository, LocalVectorAdapter()).search(
            SearchRequest(query=query, scope=scope, revision=revision, caller_roles=caller_roles, limit=limit)
        )
        return result.model_dump(mode="json")
    finally:
        repository.close()


@mcp.tool(description="解释指定的已索引符号及其真实图证据；只读且没有副作用。")
def explain_symbol(symbol: str, revision: str) -> dict[str, object]:
    return SymbolService(_REPOSITORIES).explain(symbol, revision).model_dump(mode="json")


@mcp.tool(description="基于真实图快照分析影响路径、未知边界和必需测试；只读且没有副作用。")
def analyze_impact(changed_symbol: str, revision: str, capability: str = "coupon_redemption") -> dict[str, object]:
    return ImpactService(_REPOSITORIES).analyze(changed_symbol, revision, capability).model_dump(mode="json")


@mcp.tool(description="只读分析 unified diff 的变更风险；不写入文件、不调用外部服务，且没有副作用。")
def validate_patch(diff_text: str) -> ChangeSafetyCard:
    return evaluate_change(diff_text)


@mcp.tool(description="按语义 catalog 选择必需测试；只读且没有副作用。")
def get_required_tests(capability: str, policy_id: str) -> list[dict[str, str]]:
    catalog = load_catalog(_CATALOG)
    return [item.model_dump() for item in select_required_tests(catalog, capability, policy_id)]


@mcp.tool(description="审批写入在 Phase 5 才启用；此阶段仅暴露 schema，绝不创建审批记录。")
def request_approval(change_context_id: str, requested_by: str, reason: str) -> None:
    raise ToolError("request_approval is schema-only until Phase 5; no approval was created")


@mcp.tool(description="通过共享决策管线获取变更决定；只读且没有副作用。")
def get_change_decision(diff_text: str) -> ChangeSafetyCard:
    return evaluate_change(diff_text)


if __name__ == "__main__":
    mcp.run()
