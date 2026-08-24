"""Compile a task into a layered, evidence-preserving Context Pack."""

from __future__ import annotations

from datetime import UTC
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from threading import RLock

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from bizguard.change.store import ChangeContextStore
from bizguard.impact.service import ImpactReport, ImpactService
from bizguard.graph.indexer import content_digest, index
from bizguard.graph.models import GraphSnapshot
from bizguard.knowledge.ingest import ingest_directory, knowledge_content_digest
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
    knowledge_content_digest: str
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
    candidate_confidence: float
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


def _id(task: str, repos: list[str], revisions: dict[str, str], principal: str, index_version: str, graph_digest: str, knowledge_digest: str, hint_symbols: list[str]) -> str:
    payload = {"task": task, "repos": sorted(repos), "revisions": revisions, "principal": principal, "index": index_version, "graph_content_digest": graph_digest, "knowledge_content_digest": knowledge_digest, "hint_symbols": sorted(hint_symbols)}
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
        self._reuse_lock = RLock()
        self._graphs: dict[str, GraphSnapshot] = {}
        self._knowledge_repository: KnowledgeRepository | None = None
        self._knowledge_signature: tuple[tuple[str, str], ...] | None = None

    def compile(
        self,
        task: str,
        repos: list[str],
        base_revisions: dict[str, str] | Path,
        principal: str = "engineering",
        token_budget: int = 2000,
        index_version: str = "graph-index-v1",
        hint_symbols: list[str] | None = None,
    ) -> ContextPack:
        if token_budget < 100:
            raise ValueError("token_budget must be at least 100")
        if not task.strip() or not repos:
            raise ValueError("task and repos are required")
        hints = sorted(set(hint_symbols or []))
        revisions = self._revisions(base_revisions, repos)
        revision = self._index_revision(revisions)
        graph = self._graph(revision)
        knowledge_signature = self._knowledge_digest()
        knowledge_digest = knowledge_content_digest(self._knowledge_root)
        cache_revisions = revisions | {
            "__graph_content_digest__": graph.content_digest,
            "__knowledge_content_digest__": knowledge_digest,
        }
        cache_task = task + "\n" + json.dumps(hints, ensure_ascii=False)
        key = CacheKey.create(cache_task, repos, cache_revisions, principal, index_version, token_budget)
        cached = self._cache.get(key, cache_revisions)
        if cached is not None:
            return cached
        symbol, capability, candidates, candidate_confidence = self._candidate(
            task, repos, graph, hints
        )
        impact = ImpactService(self._repositories_root, self._catalog).analyze(
            symbol, revision, capability, snapshot=graph
        )
        search = self._search(task, principal, capability, knowledge_signature)
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
        context_id = _id(
            task,
            repos,
            revisions,
            principal,
            index_version,
            graph.content_digest,
            knowledge_digest,
            hints,
        )
        retained_policy_ids = {str(item["policy_id"]) for item in mandatory.items}
        policy_recall = len(retained_policy_ids) / len(policies) if policies else 1.0
        requested_digest = revision.removeprefix("sha256:") if revision.startswith("sha256:") else None
        pack = ContextPack(
            change_context_id=context_id, task=task, repositories=sorted(repos),
            base_revisions={key: value for key, value in revisions.items() if key != "__index__"},
            index_revision=revision, graph_content_digest=graph.content_digest,
            knowledge_content_digest=knowledge_digest,
            index_version=index_version, principal=principal, token_budget=token_budget,
            token_count=_tokens({"mandatory": mandatory.model_dump(), "structural": structural.model_dump(), "rationale": rationale.model_dump(), "expandable": expandable.model_dump(), "evidence": evidence}),
            mandatory_policy_recall=policy_recall,
            stale=requested_digest is not None and requested_digest != graph.content_digest,
            mandatory=mandatory, structural=structural, rationale=rationale,
            expandable=expandable, candidates=candidates, candidate_confidence=candidate_confidence,
            impact=impact, required_tests=impact.required_tests,
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

    def _candidate(
        self,
        task: str,
        repos: list[str],
        graph: GraphSnapshot,
        hint_symbols: list[str],
    ) -> tuple[str, str, list[str], float]:
        """Fuse explicit symbol hints, lexical terms, and local vector similarity."""
        terms = set(re.findall(r"[\w-]+", task.lower()))
        candidates = [node for node in graph.nodes if any(repo in node.id for repo in repos)]
        if not candidates:
            raise ValueError("no indexed symbols in requested repositories")
        by_id = {node.id: node for node in candidates}
        matched_hints = [hint for hint in hint_symbols if hint in by_id]
        if hint_symbols and not matched_hints:
            raise ValueError("hint_symbols contain no indexed symbol in the requested repositories")

        vector = LocalVectorAdapter()

        def score(node: object) -> tuple[float, int, str]:
            text = f"{getattr(node, 'id')} {getattr(node, 'label')}".lower()
            identifier = str(getattr(node, "id"))
            quality = 0
            if "#call" not in identifier:
                quality += 2
            if "/test/" not in identifier:
                quality += 1
            if "." in str(getattr(node, "label")):
                quality += 1
            lexical = sum(term in text for term in terms)
            semantic = vector.score(task, text)
            hinted = 100.0 if identifier in matched_hints else 0.0
            return (-(hinted + lexical + semantic), -quality, identifier)

        ranked = sorted(candidates, key=score)
        selected = ranked[0]
        capability = max(
            self._catalog.capabilities,
            key=lambda item: (
                sum(term in f"{item.id} {item.name}".lower() for term in terms),
                len(set(item.repositories).intersection(repos)),
                item.id,
            ),
        )
        lexical_hits = sum(
            term in f"{selected.id} {selected.label}".lower() for term in terms
        )
        semantic_score = vector.score(task, f"{selected.id} {selected.label}")
        confidence = 1.0 if selected.id in matched_hints else min(
            0.95, lexical_hits / max(1, len(terms)) + 0.35 * semantic_score
        )
        if confidence == 0.0:
            unknown = f"unknown://task/{sha256(task.encode()).hexdigest()[:16]}"
            return unknown, capability.id, [unknown], 0.0
        candidate_ids = [selected.id]
        if confidence < 0.35:
            candidate_ids = [node.id for node in ranked[:3]]
        return selected.id, capability.id, candidate_ids, confidence

    def _search(
        self,
        task: str,
        principal: str,
        capability: str,
        knowledge_signature: tuple[tuple[str, str], ...],
    ) -> SearchResult:
        caller_roles = [role.strip() for role in principal.split(",") if role.strip()]
        if self._reuse_index:
            with self._reuse_lock:
                if (
                    self._knowledge_repository is None
                    or knowledge_signature != self._knowledge_signature
                ):
                    replacement = KnowledgeRepository.memory()
                    try:
                        ingest_directory(self._knowledge_root, replacement)
                    except Exception:
                        replacement.close()
                        raise
                    previous = self._knowledge_repository
                    self._knowledge_repository = replacement
                    self._knowledge_signature = knowledge_signature
                    if previous is not None:
                        previous.close()
                return HybridSearch(
                    self._knowledge_repository, LocalVectorAdapter(), self._catalog
                ).search(
                    SearchRequest(
                        query=task,
                        caller_roles=caller_roles,
                        scope=capability,
                        revision=self._catalog.revision,
                    )
                )
        repository = KnowledgeRepository.memory()
        try:
            ingest_directory(self._knowledge_root, repository)
            return HybridSearch(repository, LocalVectorAdapter(), self._catalog).search(
                SearchRequest(query=task, caller_roles=caller_roles, scope=capability, revision=self._catalog.revision)
            )
        finally:
            repository.close()

    def _graph(self, revision: str) -> GraphSnapshot:
        if not self._reuse_index:
            return index(self._repositories_root, revision)
        digest = content_digest(self._repositories_root)
        with self._reuse_lock:
            graph = self._graphs.get(revision)
            if graph is None or graph.content_digest != digest:
                graph = index(self._repositories_root, revision)
                self._graphs[revision] = graph
            return graph

    def _knowledge_digest(self) -> tuple[tuple[str, str], ...]:
        """Return a content-bound signature for the governed knowledge directory."""
        return tuple(
            (path.name, sha256(path.read_bytes()).hexdigest())
            for path in sorted(self._knowledge_root.glob("*.md"))
        )

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
