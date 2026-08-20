"""CLI Context commands delegate to their core services."""

import json
from pathlib import Path

from pytest import CaptureFixture

from bizguard.cli import main
from bizguard.context.compiler import ContextCompiler
from bizguard.change.store import ChangeContextStore
from bizguard.knowledge.ingest import ingest_directory
from bizguard.knowledge.models import SearchRequest
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.knowledge.search import HybridSearch, LocalVectorAdapter
from bizguard.semantic.models import load_catalog
from bizguard.semantic.required_tests import select_required_tests
from bizguard.symbols.service import SymbolService


ROOT = Path(__file__).parent.parent
REVISIONS = ROOT / "bench/fixtures/phase3-revisions.yaml"


def test_prepare_cli_delegates_to_context_compiler(capsys: CaptureFixture[str]) -> None:
    assert main(["prepare", "--task", "status", "--repos", "coupon-core", "--base-revisions", str(REVISIONS)]) == 0
    output = json.loads(capsys.readouterr().out)
    expected = ContextCompiler(ROOT / "fixtures/java-microservices").compile("status", ["coupon-core"], REVISIONS).model_dump(mode="json")
    assert output == expected
    store = ChangeContextStore(ROOT / ".artifacts" / "change-context.sqlite3")
    try:
        assert store.get(output["change_context_id"]) == json.dumps(output, ensure_ascii=False, separators=(",", ":"))
    finally:
        store.close()


def test_impact_context_cli_outputs_pack_impact(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    pack = ContextCompiler(ROOT / "fixtures/java-microservices").compile("status", ["coupon-core"], REVISIONS)
    context = tmp_path / "context.json"
    context.write_text(pack.model_dump_json(), encoding="utf-8")
    assert main(["impact", "--change-context", str(context), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == pack.impact.model_dump(mode="json")


def test_knowledge_search_cli_delegates_to_core(capsys: CaptureFixture[str]) -> None:
    assert main(["knowledge", "search", "--query", "status", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    repository = KnowledgeRepository.memory()
    try:
        ingest_directory(ROOT / "knowledge/published", repository)
        expected = HybridSearch(repository, LocalVectorAdapter()).search(
            SearchRequest(query="status", scope="coupon_redemption", revision="semantic-seed-v1", caller_roles=["engineering"])
        ).model_dump(mode="json")
    finally:
        repository.close()
    assert output == expected


def test_symbol_explain_cli_delegates_to_core(capsys: CaptureFixture[str]) -> None:
    symbol = "db://coupon-core/coupon_redemption#status"
    assert main(["symbol", "explain", "--symbol", symbol, "--revision", "phase3-fixture-v1", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    expected = SymbolService(ROOT / "fixtures/java-microservices").explain(symbol, "phase3-fixture-v1").model_dump(mode="json")
    assert output == expected


def test_required_tests_cli_delegates_to_catalog(capsys: CaptureFixture[str]) -> None:
    policy = "coupon-redemption-aggregate-idempotency-key"
    assert main(["tests", "required", "--capability", "coupon_redemption", "--policy", policy, "--json"]) == 0
    catalog = load_catalog(ROOT / "src/bizguard/semantic/catalog.yaml")
    expected = [item.model_dump() for item in select_required_tests(catalog, "coupon_redemption", policy)]
    assert json.loads(capsys.readouterr().out) == expected
