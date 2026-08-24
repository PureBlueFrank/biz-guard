"""FastMCP read adapters delegating to BizGuard's shared core services."""

from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path
import secrets
from threading import RLock
from typing import Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.server import Settings as FastMCPSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse

from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import ChangeDecision, EvaluationRequest
from bizguard.change.store import ChangeContextStore
from bizguard.context.compiler import ContextCompiler, ContextPack
from bizguard.decision import ChangeSafetyCard, evaluate_change
from bizguard.impact.service import ImpactService
from bizguard.knowledge.ingest import ingest_directory
from bizguard.knowledge.models import SearchRequest
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.knowledge.search import HybridSearch, LocalVectorAdapter
from bizguard.production import ProductionSettings
from bizguard.semantic.models import load_catalog
from bizguard.semantic.required_tests import select_required_tests
from bizguard.symbols.service import SymbolService
from bizguard.workflow.approval import ApprovalRequest, ApprovalService
from bizguard.workflow.store import SqliteApprovalStore


_ROOT = Path(__file__).parents[1]
_REPOSITORIES = _ROOT / "fixtures/java-microservices"


class _StaticTokenVerifier:
    """Authenticate one deployment principal with a constant-time token comparison."""

    def __init__(self, settings: ProductionSettings) -> None:
        if settings.api_token is None:
            raise ValueError("HTTP token verifier requires an API token")
        self._token = settings.api_token
        self._identity = settings.identity
        self._roles = list(settings.roles)

    async def verify_token(self, token: str) -> AccessToken | None:
        if not secrets.compare_digest(token, self._token):
            return None
        return AccessToken(
            token=token,
            client_id=self._identity,
            subject=self._identity,
            scopes=self._roles,
        )


_SETTINGS = ProductionSettings.from_env()
FastMCPSettings.model_rebuild()
mcp = FastMCP(
    "bizguard",
    host=_SETTINGS.host,
    port=_SETTINGS.port,
    stateless_http=_SETTINGS.transport == "streamable-http",
    json_response=_SETTINGS.transport == "streamable-http",
    token_verifier=(
        _StaticTokenVerifier(_SETTINGS)
        if _SETTINGS.transport == "streamable-http"
        else None
    ),
    auth=(
        AuthSettings(
            issuer_url=_SETTINGS.issuer_url,  # type: ignore[arg-type]
            resource_server_url=_SETTINGS.resource_url,  # type: ignore[arg-type]
            required_scopes=list(_SETTINGS.roles),
        )
        if _SETTINGS.transport == "streamable-http"
        else None
    ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", *list(_SETTINGS.allowed_hosts)],
        allowed_origins=list(_SETTINGS.allowed_origins),
    ),
)

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
_PERSISTENT_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True
)

_approval_store: SqliteApprovalStore | None = None
_change_store: ChangeContextStore | None = None
_compiler_instance: ContextCompiler | None = None
_compiler_store: ChangeContextStore | None = None
_knowledge_repository: KnowledgeRepository | None = None
_knowledge_signature: tuple[tuple[str, str], ...] | None = None
_evaluators: dict[tuple[Path, int], ChangeEvaluator] = {}
_impact_services: dict[Path, ImpactService] = {}
_knowledge_lock = RLock()


def _approval_path() -> Path:
    return Path(
        os.environ.get("BIZGUARD_APPROVAL_DB", _ROOT / ".artifacts" / "approvals.sqlite3")
    )


def _context_path() -> Path:
    return Path(
        os.environ.get("BIZGUARD_CONTEXT_DB", _ROOT / ".artifacts" / "contexts.sqlite3")
    )


def _approval_service() -> ApprovalService:
    global _approval_store
    if _approval_store is None:
        _approval_store = SqliteApprovalStore(_approval_path())
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
        _change_store = ChangeContextStore(_context_path())
    return _change_store


def _authorized_context(change_context_id: str) -> ContextPack:
    """Load a context only when the authenticated caller satisfies its ACL roles."""
    temporary_store: ChangeContextStore | None = None
    if _change_store is not None:
        store = _change_store
    else:
        path = _context_path()
        if not path.is_file():
            raise ToolError("change context unavailable")
        temporary_store = ChangeContextStore(path, read_only=True)
        store = temporary_store
    try:
        payload = store.get(change_context_id)
    finally:
        if temporary_store is not None:
            temporary_store.close()
    if payload is None:
        raise ToolError("change context unavailable")
    context = ContextPack.model_validate_json(payload)
    context_roles = {role.strip() for role in context.principal.split(",") if role.strip()}
    if not context_roles.issubset(_caller_roles()):
        raise ToolError("change context unavailable")
    return context


def _caller_roles() -> set[str]:
    """Return server-authenticated roles; resource URIs never supply their own roles."""
    access_token = get_access_token()
    if access_token is not None:
        return set(access_token.scopes)
    return {
        role.strip()
        for role in os.environ.get("BIZGUARD_CALLER_ROLES", "engineering").split(",")
        if role.strip()
    }


def _caller_identity() -> str:
    """Return the deployment-authenticated principal, never a tool argument."""
    access_token = get_access_token()
    if access_token is not None:
        return access_token.subject or access_token.client_id
    return os.environ.get("BIZGUARD_CALLER_IDENTITY", "engineering").strip()


def _caller_principal() -> str:
    """Return a stable, server-owned ACL principal for Context Compiler caching."""
    roles = sorted(_caller_roles())
    if not roles:
        raise ToolError("authenticated caller has no roles")
    return ",".join(roles)


def _compiler() -> ContextCompiler:
    global _compiler_instance, _compiler_store
    store = _change_context_store()
    if _compiler_instance is None or _compiler_store is not store:
        _compiler_instance = ContextCompiler(
            _configured_repository_root(),
            knowledge_root=_SETTINGS.governance.knowledge,
            catalog_path=_SETTINGS.governance.catalog,
            store=store,
            reuse_index=True,
        )
        _compiler_store = store
    return _compiler_instance


def _knowledge() -> KnowledgeRepository:
    """Reuse the in-memory knowledge index until a governed file changes."""
    global _knowledge_repository, _knowledge_signature
    with _knowledge_lock:
        root = _SETTINGS.governance.knowledge
        signature = tuple(
            (path.name, sha256(path.read_bytes()).hexdigest())
            for path in sorted(root.glob("*.md"))
        )
        if _knowledge_repository is None or signature != _knowledge_signature:
            replacement = KnowledgeRepository.memory()
            try:
                ingest_directory(root, replacement, quarantine_on_rejection=False)
            except Exception:
                replacement.close()
                raise
            previous = _knowledge_repository
            _knowledge_repository = replacement
            _knowledge_signature = signature
            if previous is not None:
                previous.close()
        return _knowledge_repository


def _evaluator(root: Path) -> ChangeEvaluator:
    key = (root.resolve(), id(_approval_store))
    evaluator = _evaluators.get(key)
    if evaluator is None:
        evaluator = ChangeEvaluator(
            root,
            approval_store=_approval_store,
            governance=_SETTINGS.governance,
        )
        _evaluators[key] = evaluator
    return evaluator


def _impact_service(root: Path) -> ImpactService:
    key = root.resolve()
    service = _impact_services.get(key)
    if service is None:
        service = ImpactService(key, load_catalog(_SETTINGS.governance.catalog))
        _impact_services[key] = service
    return service


@mcp.tool(description="Compile and persist a Context Pack from a task, repositories, and base revisions.", annotations=_PERSISTENT_WRITE)
def prepare_change(
    task: str | None = None,
    repos: list[str] | None = None,
    base_revisions: dict[str, str] | None = None,
    token_budget: int = 2000,
    diff_text: str | None = None,
    hint_symbols: list[str] | None = None,
) -> dict[str, object]:
    """Compile task input; ``diff_text`` returns an explicitly marked legacy decision adapter."""
    if diff_text is not None:
        governance = _SETTINGS.governance
        return {
            "legacy": True,
            "result": evaluate_change(
                diff_text,
                contract_registry_path=governance.contract_registry,
                invariants_path=governance.invariants,
                knowledge_root=governance.invariant_knowledge,
            ).model_dump(mode="json"),
        }
    if task is None or repos is None or base_revisions is None:
        raise ToolError("task, repos, and base_revisions are required for Context Pack compilation")
    return _compiler().compile(
        task,
        repos,
        base_revisions,
        _caller_principal(),
        token_budget,
        hint_symbols=hint_symbols,
    ).model_dump(mode="json")


@mcp.tool(description="Search team knowledge by ACL, revision, and scope. Read-only with no side effects.", annotations=_READ_ONLY)
def search_team_knowledge(
    query: str, scope: str, revision: str, limit: int = 5
) -> dict[str, object]:
    """Search revision-pinned knowledge under server-authenticated caller ACLs."""
    with _knowledge_lock:
        result = HybridSearch(_knowledge(), LocalVectorAdapter()).search(
            SearchRequest(
                query=query,
                scope=scope,
                revision=revision,
                caller_roles=sorted(_caller_roles()),
                limit=limit,
            )
        )
    return result.model_dump(mode="json")


@mcp.tool(description="Explain an indexed symbol and its graph evidence. Read-only with no side effects.", annotations=_READ_ONLY)
def explain_symbol(symbol: str, revision: str) -> dict[str, object]:
    """Return indexed symbol details and graph evidence."""
    return SymbolService(_configured_repository_root()).explain(symbol, revision).model_dump(mode="json")


@mcp.tool(description="Analyze impact paths, unknown boundaries, and required tests from the graph. Read-only.", annotations=_READ_ONLY)
def analyze_impact(changed_symbol: str, revision: str, capability: str = "coupon_redemption") -> dict[str, object]:
    """Analyze the impact path for an indexed symbol."""
    return _impact_service(_configured_repository_root()).analyze(
        changed_symbol, revision, capability
    ).model_dump(mode="json")


@mcp.tool(description="Validate a unified diff with deterministic policy checks. Read-only and offline.", annotations=_READ_ONLY)
def validate_patch(diff_text: str) -> ChangeSafetyCard:
    """Validate a unified diff with the deterministic policy checks."""
    return evaluate_change(diff_text)


@mcp.tool(description="Select required tests for a capability and policy from the semantic catalog. Read-only.", annotations=_READ_ONLY)
def get_required_tests(capability: str, policy_id: str) -> list[dict[str, object]]:
    """Return required tests for a capability and policy."""
    catalog = load_catalog(_SETTINGS.governance.catalog)
    return [item.model_dump() for item in select_required_tests(catalog, capability, policy_id)]


@mcp.tool(description="Create or advance a persisted approval request. This tool writes approval state.", annotations=_WRITE)
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
    _authorized_context(change_context_id)
    service = _approval_service()
    if action == "create":
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


@mcp.tool(description="Aggregate deterministic checks into a four-state decision with evidence and next actions. Read-only.", annotations=_READ_ONLY)
def get_change_decision(
    diff_text: str,
    repository_root: str | None = None,
    change_context_id: str | None = None,
    policy_revision: str = "phase5",
    base_revisions: dict[str, object] | None = None,
) -> ChangeDecision:
    """Return a canonical decision; untrusted MCP callers cannot assert CI test success."""
    root = _resolve_repository_root(repository_root)
    context = None
    if change_context_id is not None:
        context = _authorized_context(change_context_id)
        if root != _configured_repository_root():
            raise ToolError("change context repository root mismatch")
    evaluation_revisions = dict(base_revisions or {})
    if context is not None:
        context_revisions: dict[str, object] = {
            **context.base_revisions,
            "__index__": context.index_revision,
        }
        if base_revisions is not None and evaluation_revisions != context_revisions:
            raise ToolError("base_revisions do not match the persisted change context")
        evaluation_revisions = context_revisions
    approval_reader: SqliteApprovalStore | None = None
    approval_path = _approval_path()
    approval_wal = Path(f"{approval_path}-wal")
    if (
        context is not None
        and _approval_store is None
        and approval_path.is_file()
        and not approval_wal.exists()
    ):
        approval_reader = SqliteApprovalStore(approval_path, read_only=True)
    evaluator = (
        ChangeEvaluator(
            root,
            approval_store=approval_reader,
            governance=_SETTINGS.governance,
        )
        if approval_reader is not None
        else _evaluator(root)
    )
    try:
        return evaluator.evaluate(
            EvaluationRequest(
                diff_text=diff_text,
                repository_root=root,
                change_context_id=change_context_id,
                policy_revision=policy_revision,
                base_revisions=evaluation_revisions,
                prepared_required_tests=(
                    sorted(str(item.get("id")) for item in context.required_tests)
                    if context is not None
                    else None
                ),
                prepared_required_approvers=(
                    context.required_approvers if context is not None else None
                ),
            prepared_graph_content_digest=(
                context.graph_content_digest if context is not None else None
            ),
            prepared_knowledge_content_digest=(
                context.knowledge_content_digest if context is not None else None
            ),
            )
        )
    finally:
        if approval_reader is not None:
            approval_reader.close()


@mcp.resource("bizguard://changes/{change_context_id}")
def change_resource(change_context_id: str) -> dict[str, object]:
    """Summarize a persisted change context without dumping full documents."""
    try:
        context = _authorized_context(change_context_id)
    except ToolError:
        raise ToolError("resource unavailable") from None
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
    catalog = load_catalog(_SETTINGS.governance.catalog)
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

    registry = load_registry(_SETTINGS.governance.policy_registry)
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
        ingest_directory(_SETTINGS.governance.knowledge, repository)
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


@mcp.custom_route("/healthz", methods=["GET"])  # type: ignore[untyped-decorator]
async def healthz(_request: object) -> JSONResponse:
    """Return process liveness without exposing configuration or secrets."""
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])  # type: ignore[untyped-decorator]
async def readyz(_request: object) -> JSONResponse:
    """Return non-secret dependency readiness for deployment probes."""
    checks = _SETTINGS.readiness()
    ready = all(checks.values())
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status_code=200 if ready else 503,
    )


if __name__ == "__main__":
    try:
        mcp.run(transport=_SETTINGS.transport)  # type: ignore[arg-type]
    except KeyboardInterrupt:
        pass
