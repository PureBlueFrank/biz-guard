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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the fixed `bizguard check --diff FILE` command."""
    parser = argparse.ArgumentParser(prog="bizguard")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--diff", type=Path, required=True)
    impact_parser = subparsers.add_parser("impact")
    impact_subparsers = impact_parser.add_subparsers(dest="impact_command", required=True)
    analyze_parser = impact_subparsers.add_parser("analyze")
    analyze_parser.add_argument("--diff", type=Path, required=True)
    analyze_parser.add_argument("--repos", type=Path, required=True)
    analyze_parser.add_argument("--revision-set", type=Path, required=True)
    analyze_parser.add_argument("--format", choices=["json"], default="json")
    try:
        arguments = parser.parse_args(argv)
    except SystemExit:
        _print_card(_invalid_input_card("命令参数无效；请使用 bizguard check --diff FILE。"))
        return 2
    if arguments.command == "impact":
        return _impact(arguments)
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


def _impact(arguments: argparse.Namespace) -> int:
    """Build a pinned fixture snapshot and report conservative impact JSON."""
    import yaml  # type: ignore[import-untyped]
    from bizguard.eval.impact import changed_id_from_diff_text
    from bizguard.graph.indexer import index
    from bizguard.impact.analyzer import analyze

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
