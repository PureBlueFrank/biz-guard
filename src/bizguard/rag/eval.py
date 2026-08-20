"""Frozen-query Recall@k evaluation for the non-decision embedding path."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel

from bizguard.rag.embedding import (
    ZhipuEmbeddingClient,
    EmbeddingEvalResult,
    TextEmbedder,
    load_zhipu_api_key,
    retrieve_document_ids,
)
from bizguard.rag.injector import KnowledgeDocument, load_knowledge_documents


class RagEvalQuery(BaseModel):
    """One frozen natural-language query and its relevant knowledge IDs."""

    id: str
    query: str
    ground_truth_document_ids: list[str]


def load_eval_queries(path: Path) -> list[RagEvalQuery]:
    """Load and validate the frozen ten-query RAG evaluation fixture."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or loaded.get("version") != 1:
        raise ValueError("RAG evaluation fixture must contain version: 1")
    queries = loaded.get("queries")
    if not isinstance(queries, list) or len(queries) != 10:
        raise ValueError("RAG evaluation fixture must contain exactly 10 queries")
    parsed = [RagEvalQuery.model_validate(query) for query in queries]
    if len({query.id for query in parsed}) != len(parsed):
        raise ValueError("RAG evaluation query IDs must be unique")
    if any(not query.ground_truth_document_ids for query in parsed):
        raise ValueError("every RAG evaluation query needs ground truth")
    return parsed


def evaluate_recall(
    queries: list[RagEvalQuery], documents: list[KnowledgeDocument], embedder: TextEmbedder, ks: tuple[int, ...] = (1, 5)
) -> EmbeddingEvalResult:
    """Calculate whether any ground-truth document appears in each top-k result."""
    if not queries:
        raise ValueError("at least one evaluation query is required")
    known_document_ids = {document.id for document in documents}
    for query in queries:
        unknown = set(query.ground_truth_document_ids) - known_document_ids
        if unknown:
            raise ValueError(f"query {query.id} references unknown document IDs: {sorted(unknown)}")
    recall_at_k = {
        k: sum(
            bool(set(retrieve_document_ids(query.query, documents, embedder, k)) & set(query.ground_truth_document_ids))
            for query in queries
        )
        / len(queries)
        for k in ks
    }
    return EmbeddingEvalResult(
        model=embedder.model, cache_version=embedder.cache_version, recall_at_k=recall_at_k
    )


def main() -> int:
    """Run the isolated Zhipu Recall@1/Recall@5 evaluation and print JSON."""
    project_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description="Evaluate cached Zhipu embedding retrieval.")
    parser.add_argument("--queries", type=Path, default=project_root / "tests/fixtures/rag_eval_queries.yaml")
    parser.add_argument("--knowledge-dir", type=Path, default=project_root / "knowledge")
    parser.add_argument("--cache-dir", type=Path, default=project_root / ".cache/embeddings")
    arguments = parser.parse_args()
    api_key = load_zhipu_api_key()
    if api_key is None:
        parser.error("Zhipu key is unavailable in ~/.local/share/opencode/auth.json")
    result = evaluate_recall(
        load_eval_queries(arguments.queries),
        load_knowledge_documents(arguments.knowledge_dir),
        ZhipuEmbeddingClient(api_key, arguments.cache_dir),
    )
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
