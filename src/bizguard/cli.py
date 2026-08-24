"""Command-line adapter for BizGuard's shared decision function."""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from bizguard.decision import (
    ChangeSafetyCard,
    Decision,
    Fault,
    FaultCode,
    Finding,
    FindingStatus,
)
from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import ChangeDecision, EvaluationRequest
from bizguard.connectors import connect
from bizguard.decision.v2 import DecisionState
from bizguard.context.compiler import ContextCompiler, ContextPack
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
    check_parser.add_argument("--repository-root", type=Path, default=None)
    check_parser.add_argument("--base-revisions", type=Path, default=None)
    check_parser.add_argument("--change-context-id")
    check_parser.add_argument("--approval-db", type=Path)
    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.add_argument("--repository", type=Path, default=None)
    doctor_parser.add_argument("--production", action="store_true")
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
    prepare_parser.add_argument("--repository-root", type=Path, default=None)
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
    knowledge_search.add_argument("--require-real-embedding", action="store_true")
    knowledge_search.add_argument("--repository-root", type=Path, default=None)
    symbol_parser = subparsers.add_parser("symbol")
    symbol_subparsers = symbol_parser.add_subparsers(dest="symbol_command", required=True)
    symbol_explain = symbol_subparsers.add_parser("explain")
    symbol_explain.add_argument("--symbol", required=True)
    symbol_explain.add_argument("--revision", required=True)
    symbol_explain.add_argument("--json", action="store_true")
    symbol_explain.add_argument("--repository-root", type=Path, default=None)
    tests_parser = subparsers.add_parser("tests")
    tests_subparsers = tests_parser.add_subparsers(dest="tests_command", required=True)
    tests_required = tests_subparsers.add_parser("required")
    tests_required.add_argument("--capability", required=True)
    tests_required.add_argument("--policy", required=True)
    tests_required.add_argument("--json", action="store_true")
    tests_required.add_argument("--repository-root", type=Path, default=None)
    hook_parser = subparsers.add_parser("hook")
    hook_parser.add_argument("--repository", type=Path, default=None)
    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--repository", type=Path, default=None)
    init_parser.add_argument("--dry-run", action="store_true")
    init_parser.add_argument("--bootstrap", action="store_true")
    onboarding_parser = subparsers.add_parser("onboarding")
    onboarding_parser.add_argument("--repository", type=Path, default=None)
    onboarding_parser.add_argument("--bootstrap", action="store_true")
    onboarding_parser.add_argument("--dry-run", action="store_true")
    connect_parser = subparsers.add_parser("connect")
    connect_parser.add_argument("agent", choices=["claude-code", "codex"])
    connect_parser.add_argument("--repository", type=Path, default=None)
    connect_parser.add_argument("--dry-run", action="store_true")
    connect_parser.add_argument("--identity")
    connect_parser.add_argument("--roles", nargs="+")
    verify_parser = subparsers.add_parser("verify-install")
    verify_parser.add_argument("--repository", type=Path, default=None)
    verify_parser.add_argument("--offline", action="store_true")
    try:
        arguments = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            return 0
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
    if arguments.command == "hook":
        return _hook(arguments)
    if arguments.command == "init":
        return _init(arguments)
    if arguments.command == "connect":
        return _connect(arguments)
    if arguments.command == "onboarding":
        return _onboarding(arguments)
    if arguments.command == "verify-install":
        return _verify_install(arguments)
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

    try:
        result = _evaluate_change(arguments, diff_text)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid evaluation evidence: {exc}", file=sys.stderr)
        return 2
    print(result.model_dump_json())
    return _exit_code_v4(result)


def _doctor(arguments: argparse.Namespace) -> int:
    """Diagnose local prerequisites and report ok / degraded / failed per check."""
    if arguments.production:
        return _production_doctor(arguments.json)
    root = arguments.repository or _project_root()
    catalog = _local_catalog(root)
    fixture_repositories = root / "fixtures/java-microservices"
    graph_root = fixture_repositories if fixture_repositories.is_dir() else root
    checks = {
        "python": _check_python(),
        "policy": _check_policy(root / "policy" / "phase5-registry.yaml"),
        "catalog": _check_catalog(catalog),
        "mcp": _check_mcp(),
        "store": _check_store(root),
        "graph": _check_graph(graph_root),
        "ci_workflow": (
            "ok"
            if (root / ".github" / "workflows" / "bizguard.yml").is_file()
            else "degraded"
        ),
        "agent_config": _check_agent_config(root),
    }
    ok = all(not status.startswith("failed") for status in checks.values())
    degraded = any(status == "degraded" for status in checks.values())
    payload = {"ok": ok, "degraded": degraded, "checks": checks}
    print(json.dumps(payload, sort_keys=True) if arguments.json else ("ok" if ok else "failed"))
    return 0 if ok else 1


def _production_doctor(json_output: bool) -> int:
    """Check only runtime dependencies required by a deployed service."""
    from bizguard.production import ProductionSettings

    try:
        settings = ProductionSettings.from_env()
    except ValueError:
        checks = {
            "python": _check_python(),
            "production_config": "failed",
            "mcp": _check_mcp(),
        }
    else:
        checks = {
            "python": _check_python(),
            "production_config": "ok",
            "governance": _check_governance(settings.governance),
            "mcp": _check_mcp(),
            "store": _check_store(settings.repository_root),
            "graph": _check_graph(settings.repository_root),
        }
    ok = all(not status.startswith("failed") for status in checks.values())
    degraded = any(status == "degraded" for status in checks.values())
    payload = {"ok": ok, "degraded": degraded, "checks": checks}
    print(json.dumps(payload, sort_keys=True) if json_output else ("ok" if ok else "failed"))
    return 0 if ok else 1


def _check_python() -> str:
    return "ok" if sys.version_info >= (3, 12) else "failed"


def _check_catalog(path: Path | None) -> str:
    if path is None:
        return "failed"
    try:
        load_catalog(path)
    except (OSError, ValueError, yaml.YAMLError):
        return "failed"
    return "ok"


def _check_policy(path: Path) -> str:
    from bizguard.policy.registry import load_registry

    try:
        load_registry(path)
    except (OSError, ValueError, yaml.YAMLError):
        return "failed"
    return "ok"


def _check_production_config() -> str:
    from bizguard.production import ProductionSettings

    try:
        settings = ProductionSettings.from_env()
    except ValueError:
        return "failed"
    return "ok" if settings.transport == "streamable-http" and all(settings.readiness().values()) else "failed"


def _check_governance(governance: object) -> str:
    """Parse every configured governance source instead of checking paths only."""
    from bizguard.policy.invariants import load_invariants
    from bizguard.policy.registry import load_registry
    from bizguard.production import GovernancePaths
    from bizguard.rag.injector import load_contract_registry

    if not isinstance(governance, GovernancePaths):
        return "failed"
    repository = KnowledgeRepository.memory()
    try:
        load_catalog(governance.catalog)
        load_registry(governance.policy_registry)
        load_contract_registry(governance.contract_registry)
        load_invariants(
            governance.invariants,
            governance.contract_registry,
            governance.invariant_knowledge,
        )
        entries = ingest_directory(governance.knowledge, repository)
        return "ok" if entries else "failed"
    except (OSError, ValueError):
        return "failed"
    finally:
        repository.close()


def _check_mcp() -> str:
    try:
        from agents_mcp.server import mcp
        import asyncio

        if len(asyncio.run(mcp.list_tools())) != 8:
            return "degraded"
    except ImportError as exc:
        return f"failed:mcp_dependency_missing:{exc.name or 'unknown'}"
    except ValueError as exc:
        return f"failed:governance_configuration:{exc}"
    except Exception as exc:
        return f"failed:mcp_startup:{type(exc).__name__}:{exc}"
    return "ok"


def _check_store(root: Path) -> str:
    import tempfile

    from bizguard.workflow.store import SqliteApprovalStore

    try:
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteApprovalStore(Path(directory) / "approvals.sqlite3")
            store.put("probe", "probe", "a", "{}", "now")
            store.close()
    except Exception:
        return "failed"
    return "ok"


def _check_graph(repositories_root: Path) -> str:
    try:
        from bizguard.graph.indexer import index

        index(repositories_root, "production-readiness")
    except Exception:
        return "failed"
    return "ok"


def _check_agent_config(root: Path) -> str:
    has_claude = (root / ".claude" / "settings.json").is_file()
    has_codex = _codex_mcp_registered()
    return "ok" if has_claude or has_codex else "degraded"


def _hook(arguments: argparse.Namespace) -> int:
    root = arguments.repository or Path.cwd()
    diff = _git_output(root, ["diff"])
    base = _git_output(root, ["rev-parse", "HEAD"]).strip() or "unknown"
    decision = ChangeEvaluator(root).evaluate(
        EvaluationRequest(diff_text=diff, repository_root=root, base_revisions={"revision": base})
    )
    print(decision.model_dump_json())
    return _exit_code_v4(decision)


def _git_output(root: Path, args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    return result.stdout


def _init(arguments: argparse.Namespace) -> int:
    root = (arguments.repository or Path.cwd()).resolve()
    payload = {
        "languages": _detect_languages(root),
        "build_tool": _detect_build_tool(root),
        "contracts": _detect_contracts(root),
        "codeowners": (root / "CODEOWNERS").is_file() or (root / ".github" / "CODEOWNERS").is_file(),
        "agent_config": _detect_agent_config(root),
    }
    if arguments.bootstrap:
        payload["bootstrap"] = _bootstrap(root, dry_run=arguments.dry_run)
    print(json.dumps(payload, sort_keys=True))
    return 0


def _onboarding(arguments: argparse.Namespace) -> int:
    """Report compatibility and optionally create a non-destructive governance skeleton."""
    root = (arguments.repository or Path.cwd()).resolve()
    languages = _detect_languages(root)
    supported = sorted(set(languages) & {"java", "py", "proto", "yaml", "yml", "sql"})
    payload: dict[str, object] = {
        "repository": str(root),
        "supported_inputs": supported,
        "suitable": bool(supported),
        "recommendations": [
            "review generated governance files with the responsible owners",
            "start every new policy in shadow mode",
            "run bizguard doctor before enabling an agent connector",
        ],
    }
    if arguments.bootstrap:
        payload["bootstrap"] = _bootstrap(root, dry_run=arguments.dry_run)
    print(json.dumps(payload, sort_keys=True))
    return 0 if supported else 1


def _bootstrap(root: Path, *, dry_run: bool) -> dict[str, object]:
    """Create only missing starter files; never overwrite organization-owned governance."""
    templates = {
        root / "policy/phase5-registry.yaml": (
            "policies:\n"
            "  - id: replace-with-reviewed-policy\n"
            "    validator: schema_version\n"
            "    scope: replace-with-capability\n"
            "    severity: medium\n"
            "    owner: replace-with-owner\n"
            "    remediation: document the safe migration\n"
            "    required_tests: []\n"
            "    mode: shadow\n"
            "    precision: medium\n"
        ),
        root / "registry/contracts.yaml": "version: 1\ncontracts: []\n",
        root / "semantic/catalog.yaml": (
            "schema_version: 1\nrevision: bootstrap-v1\ncapabilities: []\nowners: []\n"
            "entities: []\nstates: []\ninvariants: []\npolicies: []\nrequired_tests: []\n"
        ),
        root / "knowledge/candidates/bootstrap-draft.md": (
            "---\nid: bootstrap-draft\ntitle: 待 Owner 审核的业务知识\n"
            "scope: global\nowner: replace-with-owner\n"
            "source_uri: repo://knowledge/candidates/bootstrap-draft.md\n"
            "source_revision: bootstrap-v1\nconfidence: 0.1\nsecurity_label: internal\n"
            "acl: [engineering]\nstatus: draft\npolicy_ids: []\n"
            "evidence_uri: repo://knowledge/candidates/bootstrap-draft.md\n---\n"
            "请由业务 Owner 补充事实、证据与适用边界后再发布。\n"
        ),
    }
    pending = [path for path in templates if not path.exists()]
    if not dry_run:
        for path in pending:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(templates[path], encoding="utf-8")
    return {
        "created": [] if dry_run else [str(path.relative_to(root)) for path in pending],
        "would_create": [str(path.relative_to(root)) for path in pending] if dry_run else [],
        "preserved": [str(path.relative_to(root)) for path in templates if path.exists() and path not in pending],
    }


def _detect_languages(root: Path) -> list[str]:
    suffixes: set[str] = set()
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".java", ".proto", ".yaml", ".yml", ".sql"}:
            suffixes.add(path.suffix.lstrip("."))
    return sorted(suffixes)


def _detect_build_tool(root: Path) -> list[str]:
    markers = {"pom.xml": "maven", "build.gradle": "gradle", "pyproject.toml": "python"}
    return sorted(name for filename, name in markers.items() if (root / filename).is_file())


def _detect_contracts(root: Path) -> list[str]:
    suffixes = {".proto", ".avsc", ".schema"}
    return sorted({path.suffix.lstrip(".") for path in root.rglob("*") if path.is_file() and path.suffix in suffixes})


def _detect_agent_config(root: Path) -> list[str]:
    found = []
    if (root / ".claude" / "settings.json").is_file():
        found.append("claude-code")
    if _codex_mcp_registered():
        found.append("codex")
    return found


def _codex_mcp_registered() -> bool:
    """Ask Codex itself whether the supported BizGuard MCP entry exists."""
    try:
        result = subprocess.run(
            ["codex", "mcp", "get", "bizguard", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _connect(arguments: argparse.Namespace) -> int:
    root = arguments.repository or Path.cwd()
    result = connect(
        arguments.agent,
        root,
        dry_run=arguments.dry_run,
        identity=arguments.identity,
        roles=arguments.roles,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


def _verify_install(arguments: argparse.Namespace) -> int:
    root = arguments.repository or _project_root()
    script = root / "scripts" / "verify_install.sh"
    if not script.is_file():
        print("verify_install.sh not found", file=sys.stderr)
        return 2
    args = [str(script)]
    if arguments.offline:
        args.append("--offline")
    result = subprocess.run(args, cwd=root, check=False)
    return result.returncode


def _impact(arguments: argparse.Namespace) -> int:
    """Build a pinned fixture snapshot and report conservative impact JSON."""
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


def _repository_root(requested: Path | None = None) -> Path:
    """Resolve the target repository consistently across CLI commands."""
    if requested is not None:
        return requested.resolve()
    configured = os.environ.get("BIZGUARD_REPOSITORY_ROOT")
    if configured:
        return Path(configured).resolve()
    return (_project_root() / "fixtures/java-microservices").resolve()


def _prepare(arguments: argparse.Namespace) -> int:
    compiler = ContextCompiler(_repository_root(arguments.repository_root), reuse_index=True)
    pack = compiler.compile(
        arguments.task, arguments.repos, arguments.base_revisions, arguments.principal, arguments.token_budget
    )
    print(pack.model_dump_json())
    return 0


def _context_impact(arguments: argparse.Namespace) -> int:
    pack = ContextPack.model_validate_json(arguments.change_context.read_text(encoding="utf-8"))
    print(json.dumps(pack.impact.model_dump(mode="json"), sort_keys=True))
    return 0


def _knowledge_search(arguments: argparse.Namespace) -> int:
    from bizguard.production import GovernancePaths

    root = _repository_root(arguments.repository_root)
    local_knowledge = root / "knowledge/published"
    knowledge_root = local_knowledge if local_knowledge.is_dir() else GovernancePaths.from_env().knowledge
    repository = KnowledgeRepository.memory()
    try:
        ingest_directory(knowledge_root, repository)
        result = HybridSearch(repository, LocalVectorAdapter()).search(
            SearchRequest(query=arguments.query, scope=arguments.scope, revision=arguments.revision, caller_roles=arguments.roles)
        )
        payload = result.model_dump(mode="json")
        if result.semantic_channel.startswith("DEGRADED:"):
            payload["retrieval_quality_notice"] = (
                "离线词法向量降级：结果不等同于真实 embedding；CI/生产必须配置真实 embedding。"
            )
        print(json.dumps(payload, ensure_ascii=False))
        return 1 if arguments.require_real_embedding and result.semantic_channel.startswith("DEGRADED:") else 0
    finally:
        repository.close()


def _symbol_explain(arguments: argparse.Namespace) -> int:
    result = SymbolService(_repository_root(arguments.repository_root)).explain(
        arguments.symbol, arguments.revision
    )
    print(result.model_dump_json())
    return 0


def _tests_required(arguments: argparse.Namespace) -> int:
    from bizguard.production import GovernancePaths

    root = _repository_root(arguments.repository_root)
    local_catalog = _local_catalog(root)
    catalog = load_catalog(local_catalog or GovernancePaths.from_env().catalog)
    result = [item.model_dump() for item in select_required_tests(catalog, arguments.capability, arguments.policy)]
    print(json.dumps(result, sort_keys=True))
    return 0


def _local_catalog(root: Path) -> Path | None:
    """Resolve either an onboarded project catalog or BizGuard's source-tree catalog."""
    candidates = (root / "semantic/catalog.yaml", root / "src/bizguard/semantic/catalog.yaml")
    return next((candidate for candidate in candidates if candidate.is_file()), None)


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


def _evaluate_change(arguments: argparse.Namespace, diff_text: str) -> ChangeDecision:
    root = _repository_root(arguments.repository_root)
    base_revisions: dict[str, object] = {}
    if arguments.base_revisions is not None:
        raw = yaml.safe_load(arguments.base_revisions.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            base_revisions = raw
    from bizguard.workflow.store import SqliteApprovalStore

    store = SqliteApprovalStore(arguments.approval_db) if arguments.approval_db else None
    try:
        return ChangeEvaluator(root, approval_store=store).evaluate(
            EvaluationRequest(
                diff_text=diff_text,
                repository_root=root,
                base_revisions=base_revisions,
                change_context_id=arguments.change_context_id,
            )
        )
    finally:
        if store is not None:
            store.close()


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


def _exit_code_v4(result: ChangeDecision) -> int:
    if result.decision is DecisionState.ALLOW:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
