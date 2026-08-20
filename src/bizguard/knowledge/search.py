"""Hybrid search with mandatory pre-ranking governance filters."""

from __future__ import annotations

import math
import re
from pathlib import Path

from bizguard.knowledge.models import (
    CandidateTrace,
    KnowledgeEntry,
    KnowledgeStatus,
    SearchRequest,
    SearchResult,
)
from bizguard.knowledge.rerank import rerank
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.semantic.models import SemanticCatalog, load_catalog


class LocalVectorAdapter:
    """Offline lexical-vector adapter; production callers may replace it with embedding-3."""

    model = "embedding-3"
    cache_version = "offline-hash-v1"

    def score(self, query: str, document: str) -> float:
        left, right = set(_tokens(query)), set(_tokens(document))
        return len(left & right) / math.sqrt(len(left) * len(right)) if left and right else 0.0


class HybridSearch:
    def __init__(
        self,
        repository: KnowledgeRepository,
        vector: LocalVectorAdapter | None = None,
        catalog: SemanticCatalog | None = None,
    ) -> None:
        self._repository = repository
        self._vector = vector
        self._catalog = catalog or load_catalog(
            Path(__file__).parents[1] / "semantic" / "catalog.yaml"
        )

    def search(self, request: SearchRequest) -> SearchResult:
        bm25 = self._repository.bm25(request.query)
        traces: dict[str, CandidateTrace] = {}
        eligible: list[KnowledgeEntry] = []
        for entry in self._repository.all():
            trace = traces.setdefault(entry.id, CandidateTrace(id=entry.id))
            reason = _ineligible(entry, request)
            if reason:
                trace.elimination_reason = reason
                continue
            eligible.append(entry)
            trace.bm25_score = bm25.get(entry.id, 0.0)
            trace.vector_score = (
                self._vector.score(request.query, f"{entry.title} {entry.content}")
                if self._vector
                else None
            )
        ranked = rerank({entry.id: traces[entry.id] for entry in eligible})
        ids = [trace.id for trace in ranked[: request.limit]]
        selected = [
            next(entry for entry in eligible if entry.id == identifier) for identifier in ids
        ]
        # Critical policies are scope obligations, not a consequence of their rank.
        # They are injected after the same hard filters but independently of top-k.
        mandatory = sorted(
            {
                policy
                for item in eligible
                for policy in item.policy_ids
                if self._policy_severity(policy) == "critical"
            }
        )
        return SearchResult(
            entries=selected,
            traces=list(traces.values()),
            mandatory_policy_ids=mandatory,
            semantic_channel="DEGRADED: offline lexical-vector adapter"
            if self._vector
            else "UNKNOWN",
            embedding_model=self._vector.model if self._vector else None,
            embedding_cache_version=self._vector.cache_version if self._vector else None,
        )

    def _policy_severity(self, policy_id: str) -> str | None:
        return next(
            (policy.severity for policy in self._catalog.policies if policy.id == policy_id), None
        )


def _ineligible(entry: KnowledgeEntry, request: SearchRequest) -> str | None:
    if entry.status is KnowledgeStatus.STALE or not entry.is_fresh(request.now):
        return "stale"
    if entry.status is not KnowledgeStatus.PUBLISHED:
        return "status_not_published"
    if entry.source_revision != request.revision:
        return "revision_mismatch"
    if entry.scope not in {"global", request.scope}:
        return "scope_mismatch"
    if not set(entry.acl).intersection(request.caller_roles):
        return "acl_denied"
    return None


def _tokens(value: str) -> list[str]:
    return re.findall(r"[\w-]+", value.lower(), flags=re.UNICODE)
