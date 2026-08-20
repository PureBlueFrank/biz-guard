"""Compile a task into a layered, evidence-preserving Context Pack."""

from __future__ import annotations

from datetime import UTC
from hashlib import sha256
import json
from pathlib import Path
import re

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from bizguard.change.store import ChangeContextStore
from bizguard.impact.service import ImpactReport, ImpactService
from bizguard.graph.indexer import index
from bizguard.knowledge.ingest import ingest_directory
from bizguard.knowledge.models import SearchRequest
from bizguard.knowledge.models import SearchResult
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.knowledge.search import HybridSearch, LocalVectorAdapter
from bizguard.semantic.models import load_catalog

from .cache import CacheKey, ContextCache
from .staleness import Clock, utc_now


class ContextLayer(BaseModel):
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
    required_tests: list[dict[str, str]]
    required_approvers: list[str]
    unknowns: list[str]
    evidence: list[dict[str, object]]


def _tokens(value: object) -> int:
    return len(re.findall(r"\w+", json.dumps(value, ensure_ascii=False)))


def _id(task: str, repos: list[str], revisions: dict[str, str], principal: str, index_version: str) -> str:
    payload = {"task": task, "repos": sorted(repos), "revisions": revisions, "principal": principal, "index": index_version}
    return "ctx-" + sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


class ContextCompiler:
    def __init__(
        self,
        repositories_root: Path,
        knowledge_root: Path | None = None,
        catalog_path: Path | None = None,
        cache: ContextCache | None = None,
        store: ChangeContextStore | None = None,
        now: Clock = utc_now,
    ) -> None:
        self._repositories_root = repositories_root
        root = Path(__file__).parents[3]
        self._knowledge_root = knowledge_root or root / "knowledge" / "published"
        self._catalog = load_catalog(catalog_path or root / "src/bizguard/semantic/catalog.yaml")
        self._cache, self._store, self._now = cache or ContextCache(now=now), store, now

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
        key = CacheKey.create(task, repos, revisions, principal, index_version)
        cached = self._cache.get(key, revisions)
        if cached is not None and cached.token_budget == token_budget:
            return cached
        revision = self._index_revision(revisions)
        symbol, capability = self._candidate(task, repos, revision)
        impact = ImpactService(self._repositories_root, self._catalog).analyze(symbol, revision, capability)
        search = self._search(task, principal)
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
        self._apply_budget(token_budget, mandatory, structural, rationale, expandable)
        context_id = _id(task, repos, revisions, principal, index_version)
        pack = ContextPack(
            change_context_id=context_id, task=task, repositories=sorted(repos), base_revisions=revisions,
            index_version=index_version, principal=principal, token_budget=token_budget,
            token_count=sum(_tokens(layer.items) + _tokens(layer.evidence_ids) for layer in (mandatory, structural, rationale, expandable)),
            mandatory_policy_recall=1.0, mandatory=mandatory, structural=structural, rationale=rationale,
            expandable=expandable, candidates=[symbol], impact=impact, required_tests=impact.required_tests,
            required_approvers=impact.required_approvers,
            unknowns=[impact.unknown_reason] if impact.unknown_reason else [], evidence=evidence,
        )
        self._cache.put(key, pack, revisions)
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

    def _candidate(self, task: str, repos: list[str], revision: str) -> tuple[str, str]:
        graph = index(self._repositories_root, revision)
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
        return selected.id, capability.id

    def _search(self, task: str, principal: str) -> SearchResult:
        repository = KnowledgeRepository.memory()
        try:
            ingest_directory(self._knowledge_root, repository)
            return HybridSearch(repository, LocalVectorAdapter(), self._catalog).search(
                SearchRequest(query=task, caller_roles=[principal], scope="coupon_redemption", revision=self._catalog.revision)
            )
        finally:
            repository.close()

    @staticmethod
    def _apply_budget(budget: int, mandatory: ContextLayer, *optional: ContextLayer) -> None:
        """Drop whole optional items only; Mandatory and all evidence IDs remain intact."""
        used = _tokens(mandatory.items) + _tokens(mandatory.evidence_ids)
        for layer in optional:
            retained: list[dict[str, object]] = []
            for item in layer.items:
                cost = _tokens(item)
                if used + cost <= budget:
                    retained.append(item)
                    used += cost
                else:
                    layer.truncated = True
            layer.items = retained


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
