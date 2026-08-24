"""CLI Context commands delegate to their core services."""

import json
from pathlib import Path

import pytest
from pytest import CaptureFixture

from bizguard.cli import main
from bizguard.context.compiler import ContextCompiler
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
    assert output == expected | {
        "retrieval_quality_notice": "离线词法向量降级：结果不等同于真实 embedding；CI/生产必须配置真实 embedding。"
    }


def test_prepare_help_returns_zero_without_check_incomplete(capsys: CaptureFixture[str]) -> None:
    assert main(["prepare", "--help"]) == 0
    output = capsys.readouterr().out
    assert "--token-budget" in output
    assert "CHECK_INCOMPLETE" not in output


def test_production_knowledge_mode_rejects_degraded_embedding(capsys: CaptureFixture[str]) -> None:
    assert main(["knowledge", "search", "--query", "status", "--require-real-embedding"]) == 1
    assert "retrieval_quality_notice" in capsys.readouterr().out


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


def test_onboarding_bootstrap_creates_only_missing_shadow_templates(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    (tmp_path / "app.py").write_text("print('ready')\n", encoding="utf-8")
    existing = tmp_path / "registry/contracts.yaml"
    existing.parent.mkdir(parents=True)
    existing.write_text("version: 1\ncontracts: [managed]\n", encoding="utf-8")

    assert main(["onboarding", "--repository", str(tmp_path), "--bootstrap"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["suitable"] is True
    assert "policy/phase5-registry.yaml" in payload["bootstrap"]["created"]
    assert existing.read_text(encoding="utf-8") == "version: 1\ncontracts: [managed]\n"
    assert "mode: shadow" in (tmp_path / "policy/phase5-registry.yaml").read_text(
        encoding="utf-8"
    )

    assert main(["doctor", "--repository", str(tmp_path), "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["ok"] is True
    assert doctor["checks"]["catalog"] == "ok"
    assert doctor["checks"]["graph"] == "ok"


@pytest.mark.parametrize(
    ("relative_path", "invalid_content", "check"),
    [
        ("semantic/catalog.yaml", "capabilities: wrong\n", "catalog"),
        ("policy/phase5-registry.yaml", "policies: wrong\n", "policy"),
    ],
)
def test_doctor_parses_local_governance_instead_of_only_checking_file_exists(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    relative_path: str,
    invalid_content: str,
    check: str,
) -> None:
    (tmp_path / "app.py").write_text("print('ready')\n", encoding="utf-8")
    assert main(["onboarding", "--repository", str(tmp_path), "--bootstrap"]) == 0
    capsys.readouterr()
    target = tmp_path / relative_path
    target.write_text(invalid_content, encoding="utf-8")

    assert main(["doctor", "--repository", str(tmp_path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["checks"][check] == "failed"
