"""Command-line adapter for BizGuard's shared decision function."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from bizguard.decision import (
    ChangeSafetyCard,
    Decision,
    Fault,
    FaultCode,
    Finding,
    FindingStatus,
    evaluate_change,
)
from bizguard.context.compiler import ContextCompiler, ContextPack
from bizguard.change.store import ChangeContextStore
from bizguard.knowledge.ingest import ingest_directory
from bizguard.knowledge.models import SearchRequest
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.knowledge.search import HybridSearch, LocalVectorAdapter
from bizguard.semantic.models import load_catalog
from bizguard.semantic.required_tests import select_required_tests
from bizguard.symbols.service import SymbolService


def main(argv: Sequence[str] | None = None) -> int:
    """Run the fixed `bizguard check --diff FILE` command."""
    parser = argparse.ArgumentParser(prog="bizguard")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--diff", type=Path, required=True)
    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--json", action="store_true")
    impact_parser = subparsers.add_parser("impact")
    impact_subparsers = impact_parser.add_subparsers(dest="impact_command")
    analyze_parser = impact_subparsers.add_parser("analyze")
    analyze_parser.add_argument("--diff", type=Path, required=True)
    analyze_parser.add_argument("--repos", type=Path, required=True)
    analyze_parser.add_argument("--revision-set", type=Path, required=True)
    analyze_parser.add_argument("--format", choices=["json"], default="json")
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--task", required=True)
    prepare_parser.add_argument("--repos", nargs="+", required=True)
    prepare_parser.add_argument("--base-revisions", type=Path, required=True)
    prepare_parser.add_argument("--principal", default="engineering")
    prepare_parser.add_argument("--token-budget", type=int, default=2000)
    prepare_parser.add_argument("--json", action="store_true")
    impact_parser.add_argument("--change-context", type=Path)
    impact_parser.add_argument("--json", action="store_true")
    knowledge_parser = subparsers.add_parser("knowledge")
    knowledge_subparsers = knowledge_parser.add_subparsers(dest="knowledge_command", required=True)
    knowledge_search = knowledge_subparsers.add_parser("search")
    knowledge_search.add_argument("--query", required=True)
    knowledge_search.add_argument("--scope", default="coupon_redemption")
    knowledge_search.add_argument("--revision", default="semantic-seed-v1")
    knowledge_search.add_argument("--roles", nargs="+", default=["engineering"])
    knowledge_search.add_argument("--json", action="store_true")
    symbol_parser = subparsers.add_parser("symbol")
    symbol_subparsers = symbol_parser.add_subparsers(dest="symbol_command", required=True)
    symbol_explain = symbol_subparsers.add_parser("explain")
    symbol_explain.add_argument("--symbol", required=True)
    symbol_explain.add_argument("--revision", required=True)
    symbol_explain.add_argument("--json", action="store_true")
    tests_parser = subparsers.add_parser("tests")
    tests_subparsers = tests_parser.add_subparsers(dest="tests_command", required=True)
    tests_required = tests_subparsers.add_parser("required")
    tests_required.add_argument("--capability", required=True)
    tests_required.add_argument("--policy", required=True)
    tests_required.add_argument("--json", action="store_true")
    try:
        arguments = parser.parse_args(argv)
    except SystemExit:
        _print_card(_invalid_input_card("命令参数无效；请使用 bizguard check --diff FILE。"))
        return 2
    if arguments.command == "prepare":
        return _prepare(arguments)
    if arguments.command == "doctor":
        return _doctor(arguments)
    if arguments.command == "impact" and arguments.change_context:
        return _context_impact(arguments)
    if arguments.command == "impact":
        return _impact(arguments)
    if arguments.command == "knowledge":
        return _knowledge_search(arguments)
    if arguments.command == "symbol":
        return _symbol_explain(arguments)
    if arguments.command == "tests":
        return _tests_required(arguments)
    diff_path = arguments.diff
    if not diff_path.is_file() or not diff_path.stat().st_mode:
        print("--diff must identify an existing readable unified diff file", file=sys.stderr)
        _print_card(_invalid_input_card("--diff 必须指向存在且可读的文件。", [str(diff_path)]))
        return 2
    try:
        diff_text = diff_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"unable to read --diff: {exc}", file=sys.stderr)
        _print_card(_invalid_input_card("无法读取 diff 文件。", [str(diff_path)]))
        return 2

    card = evaluate_change(diff_text)
    _print_card(card)
    return _exit_code(card)


def _doctor(arguments: argparse.Namespace) -> int:
    """Diagnose local, read-only prerequisites without contacting any service."""
    root = _project_root()
    checks = {
        "policy": (root / "policy" / "invariants.yaml").is_file(),
        "catalog": (root / "src/bizguard/semantic/catalog.yaml").is_file(),
        "mcp": True,
    }
    payload = {"ok": all(checks.values()), "checks": checks}
    print(json.dumps(payload, sort_keys=True) if arguments.json else ("ok" if payload["ok"] else "failed"))
    return 0 if payload["ok"] else 1


def _impact(arguments: argparse.Namespace) -> int:
    """Build a pinned fixture snapshot and report conservative impact JSON."""
    import yaml  # type: ignore[import-untyped]
    from bizguard.eval.impact import changed_id_from_diff_text
    from bizguard.graph.indexer import index
    from bizguard.impact.analyzer import analyze

    if not getattr(arguments, "impact_command", None):
        print("impact requires --change-context or the legacy analyze subcommand", file=sys.stderr)
        return 2
    raw = yaml.safe_load(arguments.revision_set.read_text(encoding="utf-8")) or {}
    revision = str(raw.get("revision", "phase3-fixture-v1"))
    diff_text = arguments.diff.read_text(encoding="utf-8")
    snapshot = index(arguments.repos, revision)
    changed = changed_id_from_diff_text(snapshot, diff_text)
    result = analyze(snapshot, changed, revision)
    print(
        json.dumps(
            {
                "layers": result.layers,
                "path": result.path,
                "unknown_boundary": result.unknown_boundary,
                "evidence": [item.model_dump() for item in result.evidence],
            },
            sort_keys=True,
        )
    )
    return 0


def _project_root() -> Path:
    return Path(__file__).parents[2]


def _prepare(arguments: argparse.Namespace) -> int:
    root = _project_root()
    store = ChangeContextStore(root / ".artifacts" / "change-context.sqlite3")
    try:
        compiler = ContextCompiler(root / "fixtures/java-microservices", store=store)
        pack = compiler.compile(
            arguments.task, arguments.repos, arguments.base_revisions, arguments.principal, arguments.token_budget
        )
        print(pack.model_dump_json())
        return 0
    finally:
        store.close()


def _context_impact(arguments: argparse.Namespace) -> int:
    pack = ContextPack.model_validate_json(arguments.change_context.read_text(encoding="utf-8"))
    print(json.dumps(pack.impact.model_dump(mode="json"), sort_keys=True))
    return 0


def _knowledge_search(arguments: argparse.Namespace) -> int:
    root = _project_root()
    repository = KnowledgeRepository.memory()
    try:
        ingest_directory(root / "knowledge/published", repository)
        result = HybridSearch(repository, LocalVectorAdapter()).search(
            SearchRequest(query=arguments.query, scope=arguments.scope, revision=arguments.revision, caller_roles=arguments.roles)
        )
        print(result.model_dump_json())
        return 0
    finally:
        repository.close()


def _symbol_explain(arguments: argparse.Namespace) -> int:
    root = _project_root()
    result = SymbolService(root / "fixtures/java-microservices").explain(arguments.symbol, arguments.revision)
    print(result.model_dump_json())
    return 0


def _tests_required(arguments: argparse.Namespace) -> int:
    catalog = load_catalog(_project_root() / "src/bizguard/semantic/catalog.yaml")
    result = [item.model_dump() for item in select_required_tests(catalog, arguments.capability, arguments.policy)]
    print(json.dumps(result, sort_keys=True))
    return 0


def _invalid_input_card(message: str, refs: list[str] | None = None) -> ChangeSafetyCard:
    return ChangeSafetyCard(
        decision=Decision.CHECK_INCOMPLETE,
        findings=[
            Finding(
                finding_id="fault:input_validation",
                status=FindingStatus.INCOMPLETE,
                message=message,
                evidence_refs=refs or [],
            )
        ],
        faults=[Fault(code=FaultCode.INPUT_VALIDATION, message=message, evidence_refs=refs or [])],
    )


def _print_card(card: ChangeSafetyCard) -> None:
    print(card.model_dump_json())


def _exit_code(card: ChangeSafetyCard) -> int:
    if card.decision is Decision.ALLOW:
        return 0
    if card.decision is Decision.BLOCK:
        return 1
    fault_codes = {fault.code for fault in card.faults}
    return {
        FaultCode.INPUT_VALIDATION: 2,
        FaultCode.DIFF_PARSE: 3,
        FaultCode.POLICY_UNCOVERED: 4,
        FaultCode.RETRIEVAL_EMPTY: 5,
        FaultCode.EMBEDDING_TIMEOUT: 6,
        FaultCode.CACHE_CORRUPT: 7,
    }.get(next(iter(fault_codes), FaultCode.DIFF_PARSE), 3)


if __name__ == "__main__":
    raise SystemExit(main())
