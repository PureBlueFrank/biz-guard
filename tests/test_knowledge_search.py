from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from bizguard.eval.retrieval import evaluate
from bizguard.knowledge.ingest import ingest_directory
from bizguard.knowledge.models import SearchRequest
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.knowledge.search import HybridSearch, LocalVectorAdapter


@pytest.fixture()
def search() -> HybridSearch:
    repo = KnowledgeRepository.memory()
    ingest_directory(Path(__file__).parents[1] / "knowledge/published", repo)
    return HybridSearch(repo, LocalVectorAdapter())


@pytest.mark.parametrize(("query", "first"), [("idempotency_key", "field-idempotency-key"), ("ledger status", "field-ledger-status"), ("Redeem public API", "adr-api-contract"), ("CouponService protobuf", "proto-redeem")])
def test_exact_bm25_first_result(search: HybridSearch, query: str, first: str) -> None:
    result = search.search(SearchRequest(query=query, caller_roles=["engineering"], scope="coupon_redemption", revision="semantic-seed-v1"))
    assert result.entries[0].id == first
    assert result.traces


@pytest.mark.parametrize(("query", "gold"), [("duplicate timeout replay", "adr-double-redeem"), ("reflection mapping", "dynamic-mapper-boundary"), ("merchant event consumer", "merchant-consumer")])
def test_semantic_gold_is_recalled(search: HybridSearch, query: str, gold: str) -> None:
    result = search.search(SearchRequest(query=query, caller_roles=["engineering"], scope="coupon_redemption", revision="semantic-seed-v1"))
    assert gold in [item.id for item in result.entries]


def test_acl_and_stale_are_eliminated_before_results(search: HybridSearch) -> None:
    result = search.search(SearchRequest(query="incident legacy", caller_roles=["engineering"], scope="coupon_redemption", revision="semantic-seed-v1"))
    assert {item.id for item in result.entries}.isdisjoint({"restricted-incident", "stale-ledger"})
    reasons = {item.id: item.elimination_reason for item in result.traces}
    assert reasons["restricted-incident"] == "acl_denied" and reasons["stale-ledger"] == "stale"


def test_evaluator_rejects_wrong_golden_order(tmp_path: Path) -> None:
    original = Path(__file__).parents[1] / "bench/golden/retrieval/phase2.yaml"
    payload = yaml.safe_load(original.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    payload["tasks"][0]["expected_ids"].reverse()
    broken = tmp_path / "broken.yaml"
    broken.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="golden mismatch"):
        evaluate(broken, Path(__file__).parents[1] / "knowledge/published")
