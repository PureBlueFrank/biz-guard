"""Compile a task into a layered, evidence-preserving Context Pack."""

from __future__ import annotations

from datetime import UTC
from hashlib import sha256
import json
import math
from pathlib import Path
import re

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from bizguard.change.store import ChangeContextStore
from bizguard.impact.service import ImpactReport, ImpactService
from bizguard.graph.indexer import index
from bizguard.graph.models import GraphSnapshot
from bizguard.knowledge.ingest import ingest_directory
from bizguard.knowledge.models import SearchRequest
from bizguard.knowledge.models import SearchResult
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.knowledge.search import HybridSearch, LocalVectorAdapter
from bizguard.semantic.models import load_catalog

from .cache import CacheKey, ContextCache
from .staleness import Clock, utc_now


class ContextLayer(BaseModel):
    """Model one ordered layer of a compiled Context Pack."""

    name: str
    items: list[dict[str, object]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    truncated: bool = False


class ContextPack(BaseModel):
    """A compact, immutable summary. Evidence IDs are never budget-truncated."""

    change_context_id: str
    task: str
    repositories: list[str]
    base_revisions: dict[str, str]
    index_revision: str
    graph_content_digest: str
    index_version: str
    principal: str
    token_budget: int
    token_count: int
    mandatory_policy_recall: float
    stale: bool = False
    mandatory: ContextLayer
    structural: ContextLayer
    rationale: ContextLayer
    expandable: ContextLayer
    candidates: list[str]
    impact: ImpactReport
    required_tests: list[dict[str, object]]
    required_approvers: list[str]
    unknowns: list[str]
    evidence: list[dict[str, object]]


def _tokens(value: object) -> int:
    """Estimate serialized tokens while counting CJK characters individually."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    units = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\sA-Za-z0-9_\u4e00-\u9fff]", text)
    return sum(
        1 if len(unit) == 1 else math.ceil(len(unit.encode("utf-8")) / 4)
        for unit in units
    )


def _id(task: str, repos: list[str], revisions: dict[str, str], principal: str, index_version: str, digest: str) -> str:
    payload = {"task": task, "repos": sorted(repos), "revisions": revisions, "principal": principal, "index": index_version, "graph_content_digest": digest}
    return "ctx-" + sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


class ContextCompiler:
    """Compile tasks into evidence-preserving Context Packs."""

    def __init__(
        self,
        repositories_root: Path,
        knowledge_root: Path | None = None,
        catalog_path: Path | None = None,
        cache: ContextCache | None = None,
        store: ChangeContextStore | None = None,
        now: Clock = utc_now,
        reuse_index: bool = False,
    ) -> None:
        self._repositories_root = repositories_root
        root = Path(__file__).parents[3]
        self._knowledge_root = knowledge_root or root / "knowledge" / "published"
        self._catalog = load_catalog(catalog_path or root / "src/bizguard/semantic/catalog.yaml")
        self._cache, self._store, self._now = cache or ContextCache(now=now), store, now
        self._reuse_index = reuse_index
        self._graphs: dict[str, GraphSnapshot] = {}
        self._knowledge_repository: KnowledgeRepository | None = None

    def compile(
        self,
        task: str,
        repos: list[str],
        base_revisions: dict[str, str] | Path,
        principal: str = "engineering",
        token_budget: int = 2000,
        index_version: str = "graph-index-v1",
    ) -> ContextPack:
        if token_budget not in {800, 1200, 2000, 4000}:
            raise ValueError("token_budget must be one of 800, 1200, 2000, 4000")
        if not task.strip() or not repos:
            raise ValueError("task and repos are required")
        revisions = self._revisions(base_revisions, repos)
        revision = self._index_revision(revisions)
        graph = self._graph(revision)
        cache_revisions = revisions | {"__graph_content_digest__": graph.content_digest}
        key = CacheKey.create(task, repos, cache_revisions, principal, index_version, token_budget)
        cached = self._cache.get(key, cache_revisions)
        if cached is not None:
            return cached
        symbol, capability = self._candidate(task, repos, graph)
        impact = ImpactService(self._repositories_root, self._catalog).analyze(symbol, revision, capability)
        search = self._search(task, principal, capability)
        policies = [item for item in self._catalog.policies if item.capability == capability]
        mandatory_items: list[dict[str, object]] = [
            {"policy_id": item.id, "severity": item.severity, "invariant": item.invariant,
             "statement": next(inv.statement for inv in self._catalog.invariants if inv.id == item.invariant)}
            for item in policies
        ]
        evidence = impact.evidence + [
            {"id": f"knowledge:{entry.id}", "source": "knowledge", "confidence": entry.confidence,
             "revision": entry.source_revision, "evidence_uri": entry.evidence_uri}
            for entry in search.entries
        ]
        mandatory = ContextLayer(name="Mandatory", items=mandatory_items, evidence_ids=[str(item["id"]) for item in evidence])
        structural = ContextLayer(name="Structural", items=[{"candidate": symbol, "path": impact.path, "layers": impact.layers}])
        rationale = ContextLayer(name="Rationale", items=[
            {"knowledge_id": entry.id, "title": entry.title, "summary": entry.content}
            for entry in search.entries
        ])
        expandable_items: list[dict[str, object]] = [
            {"candidate_trace": trace.model_dump(mode="json")} for trace in search.traces
        ]
        expandable_items.append({"semantic_channel": search.semantic_channel})
        expandable = ContextLayer(name="Expandable", items=expandable_items)
        self._apply_budget(token_budget, mandatory, structural, rationale, expandable, evidence)
        context_id = _id(task, repos, revisions, principal, index_version, graph.content_digest)
        retained_policy_ids = {str(item["policy_id"]) for item in mandatory.items}
        policy_recall = len(retained_policy_ids) / len(policies) if policies else 1.0
        requested_digest = revision.removeprefix("sha256:") if revision.startswith("sha256:") else None
        pack = ContextPack(
            change_context_id=context_id, task=task, repositories=sorted(repos),
            base_revisions={key: value for key, value in revisions.items() if key != "__index__"},
            index_revision=revision, graph_content_digest=graph.content_digest,
            index_version=index_version, principal=principal, token_budget=token_budget,
            token_count=_tokens({"mandatory": mandatory.model_dump(), "structural": structural.model_dump(), "rationale": rationale.model_dump(), "expandable": expandable.model_dump(), "evidence": evidence}),
            mandatory_policy_recall=policy_recall,
            stale=requested_digest is not None and requested_digest != graph.content_digest,
            mandatory=mandatory, structural=structural, rationale=rationale,
            expandable=expandable, candidates=[symbol], impact=impact, required_tests=impact.required_tests,
            required_approvers=impact.required_approvers,
            unknowns=( ["NO_MATCHING_SYMBOL"] if symbol.startswith("unknown://task/") else [] )
            + ([impact.unknown_reason] if impact.unknown_reason else []), evidence=evidence,
        )
        self._cache.put(key, pack, cache_revisions)
        if self._store:
            self._store.put(context_id, pack.model_dump_json(), self._now().astimezone(UTC).isoformat())
        return pack

    def _revisions(self, raw: dict[str, str] | Path, repos: list[str]) -> dict[str, str]:
        if isinstance(raw, Path):
            data = yaml.safe_load(raw.read_text(encoding="utf-8")) or {}
            values = data.get("repositories", data)
            if not isinstance(values, dict):
                raise ValueError("base revisions must be a mapping")
            revisions = {str(key): str(value) for key, value in values.items()}
            global_revision = data.get("revision")
            if global_revision:
                revisions.setdefault("__index__", str(global_revision))
        else:
            revisions = dict(raw)
        missing = set(repos) - set(revisions)
        if missing:
            raise ValueError(f"missing base revisions for: {', '.join(sorted(missing))}")
        return dict(sorted(revisions.items()))

    @staticmethod
    def _index_revision(revisions: dict[str, str]) -> str:
        return revisions.get("__index__") or sha256(json.dumps(revisions, sort_keys=True).encode()).hexdigest()[:16]

    def _candidate(self, task: str, repos: list[str], graph: GraphSnapshot) -> tuple[str, str]:
        """Select by lexical graph evidence; private-method intent is not source-visibility aware yet.

        P4 has no Java visibility facts, so tasks such as ``rename private redeem helper``
        may legitimately resolve to the highest lexical graph node rather than a private
        method.  P5 must add visibility facts before this can be made semantic.
        """
        terms = set(re.findall(r"[\w-]+", task.lower()))
        candidates = [node for node in graph.nodes if any(repo in node.id for repo in repos)]
        if not candidates:
            raise ValueError("no indexed symbols in requested repositories")
        def score(node: object) -> tuple[int, int, str]:
            text = f"{getattr(node, 'id')} {getattr(node, 'label')}".lower()
            identifier = str(getattr(node, "id"))
            quality = 0
            if "#call" not in identifier:
                quality += 2
            if "/test/" not in identifier:
                quality += 1
            if "." in str(getattr(node, "label")):
                quality += 1
            return (-sum(term in text for term in terms), -quality, identifier)
        selected = sorted(candidates, key=score)[0]
        capability = max(
            self._catalog.capabilities,
            key=lambda item: (
                sum(term in f"{item.id} {item.name}".lower() for term in terms),
                len(set(item.repositories).intersection(repos)),
                item.id,
            ),
        )
        if not any(term in f"{selected.id} {selected.label}".lower() for term in terms):
            return f"unknown://task/{sha256(task.encode()).hexdigest()[:16]}", capability.id
        return selected.id, capability.id

    def _search(self, task: str, principal: str, capability: str) -> SearchResult:
        if self._reuse_index:
            if self._knowledge_repository is None:
                self._knowledge_repository = KnowledgeRepository.memory()
                ingest_directory(self._knowledge_root, self._knowledge_repository)
            return HybridSearch(self._knowledge_repository, LocalVectorAdapter(), self._catalog).search(
                SearchRequest(query=task, caller_roles=[principal], scope=capability, revision=self._catalog.revision)
            )
        repository = KnowledgeRepository.memory()
        try:
            ingest_directory(self._knowledge_root, repository)
            return HybridSearch(repository, LocalVectorAdapter(), self._catalog).search(
                SearchRequest(query=task, caller_roles=[principal], scope=capability, revision=self._catalog.revision)
            )
        finally:
            repository.close()

    def _graph(self, revision: str) -> GraphSnapshot:
        if not self._reuse_index:
            return index(self._repositories_root, revision)
        graph = self._graphs.get(revision)
        if graph is None:
            graph = index(self._repositories_root, revision)
            self._graphs[revision] = graph
        return graph

    @staticmethod
    def _apply_budget(
        budget: int,
        mandatory: ContextLayer,
        structural: ContextLayer,
        rationale: ContextLayer,
        expandable: ContextLayer,
        evidence: list[dict[str, object]],
    ) -> None:
        """Trim least important layers until their serialized payload fits the budget.

        Mandatory policy and evidence references are intentionally never removed. The
        Expandable and rationale are trimmed first.  Detailed evidence and structural
        items are optional renderings; their immutable IDs stay in Mandatory even
        when their verbose copies must be omitted at smaller budgets.
        """
        def payload_tokens() -> int:
            return _tokens(
                {
                    "mandatory": mandatory.model_dump(),
                    "structural": structural.model_dump(),
                    "rationale": rationale.model_dump(),
                    "expandable": expandable.model_dump(), "evidence": evidence,
                }
            )

        for layer in (expandable, rationale):
            while layer.items and payload_tokens() > budget:
                layer.items.pop()
                layer.truncated = True
        while evidence and payload_tokens() > budget:
            evidence.pop()
        while structural.items and payload_tokens() > budget:
            structural.items.pop()
            structural.truncated = True
        if payload_tokens() > budget:
            raise ValueError("mandatory context exceeds token_budget")


def compile_context(
    task: str,
    repos: list[str],
    base_revisions: dict[str, str] | Path,
    principal: str = "engineering",
    token_budget: int = 2000,
    index_version: str = "graph-index-v1",
) -> ContextPack:
    """Convenience entrypoint for callers that do not need a persistent compiler."""
    root = Path(__file__).parents[3]
    return ContextCompiler(root / "fixtures/java-microservices").compile(
        task, repos, base_revisions, principal, token_budget, index_version
    )
