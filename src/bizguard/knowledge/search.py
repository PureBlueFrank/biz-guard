"""Hybrid search with mandatory pre-ranking governance filters."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Protocol

from bizguard.knowledge.models import (
    CandidateTrace,
    KnowledgeEntry,
    KnowledgeStatus,
    SearchRequest,
    SearchResult,
)
from bizguard.knowledge.rerank import rerank
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.rag.embedding import EmbeddingError, TextEmbedder
from bizguard.semantic.models import SemanticCatalog, load_catalog


class VectorAdapter(Protocol):
    """Batch semantic-scoring seam used after governance filters."""

    model: str
    cache_version: str
    semantic_channel: str

    def score_many(self, query: str, documents: list[str]) -> list[float]:
        """Return one score per document in input order."""


class LocalVectorAdapter:
    """Offline lexical-vector adapter; production callers may replace it with embedding-3."""

    model = "embedding-3"
    cache_version = "offline-hash-v1"
    semantic_channel = "DEGRADED: offline lexical-vector adapter"

    def score(self, query: str, document: str) -> float:
        left, right = set(_tokens(query)), set(_tokens(document))
        return len(left & right) / math.sqrt(len(left) * len(right)) if left and right else 0.0

    def score_many(self, query: str, documents: list[str]) -> list[float]:
        """Score offline documents without network access."""
        return [self.score(query, document) for document in documents]


class EmbeddingVectorAdapter:
    """Score governed documents with a real batch embedding provider."""

    semantic_channel = "REAL: zhipu embedding-3"

    def __init__(self, embedder: TextEmbedder) -> None:
        self._embedder = embedder
        self.model = embedder.model
        self.cache_version = embedder.cache_version

    def score_many(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        vectors = self._embedder.embed([query, *documents])
        if len(vectors) != len(documents) + 1:
            raise EmbeddingError("embedder returned a vector count different from its inputs")
        query_vector = vectors[0]
        return [_cosine(query_vector, vector) for vector in vectors[1:]]


class HybridSearch:
    """Search governed knowledge using BM25 and optional vector scores."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        vector: VectorAdapter | None = None,
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
        if self._vector is not None:
            vector_scores = self._vector.score_many(
                request.query,
                [f"{entry.title} {entry.content}" for entry in eligible],
            )
            if len(vector_scores) != len(eligible):
                raise EmbeddingError("vector adapter returned a score count different from candidates")
            for entry, score in zip(eligible, vector_scores, strict=True):
                traces[entry.id].vector_score = score
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
            semantic_channel=self._vector.semantic_channel if self._vector else "UNKNOWN",
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


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise EmbeddingError("embedding vectors must have matching non-empty dimensions")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise EmbeddingError("embedding vectors must not be zero")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
