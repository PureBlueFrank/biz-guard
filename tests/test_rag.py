"""Tests for full-text decision retrieval and isolated embedding evaluation."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bizguard.diff_parser import ParsedDiff, ParsedFile
from bizguard.rag.embedding import (
    CACHE_VERSION,
    EMBEDDING_MODEL,
    ZhipuEmbeddingClient,
    load_zhipu_api_key,
)
from bizguard.rag.eval import evaluate_recall, load_eval_queries
from bizguard.rag.injector import inject_full_text, load_contract_registry, load_knowledge_documents


PROJECT_ROOT = Path(__file__).parent.parent


class KeywordEmbedder:
    """Deterministic local embedder for retrieval wiring tests."""

    model = "test-keyword-embedder"
    cache_version = "test-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vocabulary = ("幂等", "日志", "Python", "账本", "重复")
        return [[float(token in text) for token in vocabulary] + [1.0] for text in texts]


def test_full_text_injection_contains_every_knowledge_document_for_matching_contract() -> None:
    registry = load_contract_registry(PROJECT_ROOT / "registry" / "contracts.yaml")
    documents = load_knowledge_documents(PROJECT_ROOT / "knowledge")
    parsed_diff = ParsedDiff(
        files=[ParsedFile(old_path=None, new_path=registry[0].source, operation="modify", hunks=[])]
    )

    evidence = inject_full_text(parsed_diff, registry, documents)

    assert evidence.contract_ids == ["coupon-redemption-idempotency-key"]
    assert evidence.knowledge_document_ids == [document.id for document in documents]
    assert all(document.id in evidence.full_text for document in documents)


def test_full_text_injection_returns_no_fabricated_evidence_without_a_contract() -> None:
    parsed_diff = ParsedDiff(
        files=[
            ParsedFile(
                old_path=None, new_path="sample/coupon-service/unknown.py", operation="modify", hunks=[]
            )
        ]
    )
    evidence = inject_full_text(parsed_diff, [], load_knowledge_documents(PROJECT_ROOT / "knowledge"))
    assert evidence == evidence.__class__(contract_ids=[], knowledge_document_ids=[], full_text="")


def test_frozen_eval_fixture_has_ten_queries_and_covers_every_knowledge_document() -> None:
    queries = load_eval_queries(PROJECT_ROOT / "tests" / "fixtures" / "rag_eval_queries.yaml")
    document_ids = {document.id for document in load_knowledge_documents(PROJECT_ROOT / "knowledge")}
    ground_truth_ids = {document_id for query in queries for document_id in query.ground_truth_document_ids}
    assert len(queries) == 10
    assert document_ids <= ground_truth_ids


def test_embedding_evaluation_wiring_calculates_recall_at_one_and_five() -> None:
    documents = load_knowledge_documents(PROJECT_ROOT / "knowledge")
    queries = load_eval_queries(PROJECT_ROOT / "tests" / "fixtures" / "rag_eval_queries.yaml")
    result = evaluate_recall(queries, documents, KeywordEmbedder())
    assert result.model == "test-keyword-embedder"
    assert set(result.recall_at_k) == {1, 5}
    assert all(0.0 <= value <= 1.0 for value in result.recall_at_k.values())


def test_embedding_client_reuses_valid_cache_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = ZhipuEmbeddingClient("not-a-real-key", tmp_path)
    texts = ["cached text"]
    cache_path = client._cache_path(texts)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {"cache_version": CACHE_VERSION, "model": EMBEDDING_MODEL, "texts": texts, "vectors": [[1, 2]]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("bizguard.rag.embedding.httpx.post", lambda *args, **kwargs: pytest.fail("network used"))
    assert client.embed(texts) == [[1.0, 2.0]]


def test_zhipu_embedding_live_call_when_local_credentials_are_available() -> None:
    """Exercise the real API when the documented local credential is available."""
    api_key = load_zhipu_api_key()
    if api_key is None:
        pytest.skip("Zhipu API key is not configured locally")
    client = ZhipuEmbeddingClient(api_key, PROJECT_ROOT / ".cache" / "embeddings")
    try:
        vectors = client.embed(["优惠券核销幂等性"])
    except httpx.RequestError as error:
        pytest.skip(f"Zhipu embedding endpoint is unavailable: {error}")
    assert len(vectors) == 1
    assert len(vectors[0]) == 2048
