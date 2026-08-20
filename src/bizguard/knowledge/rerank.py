"""Explainable deterministic fusion for lexical and vector retrieval."""

from __future__ import annotations

from bizguard.knowledge.models import CandidateTrace


def rerank(scores: dict[str, CandidateTrace]) -> list[CandidateTrace]:
    """Fuse normalized channel scores; deterministic ID breaks make goldens reproducible."""
    for trace in scores.values():
        lexical = trace.bm25_score or 0.0
        semantic = trace.vector_score or 0.0
        trace.rerank_score = 0.65 * lexical + 0.35 * semantic
    return sorted(scores.values(), key=lambda item: (-(item.rerank_score or 0), item.id))
