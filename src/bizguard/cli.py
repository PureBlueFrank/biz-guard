"""Command-line adapter for BizGuard's shared decision function."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from bizguard.decision import ChangeSafetyCard, Decision, Fault, FaultCode, Finding, FindingStatus, evaluate_change


def main(argv: Sequence[str] | None = None) -> int:
    """Run the fixed `bizguard check --diff FILE` command."""
    parser = argparse.ArgumentParser(prog="bizguard")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--diff", type=Path, required=True)
    try:
        arguments = parser.parse_args(argv)
    except SystemExit:
        _print_card(_invalid_input_card("命令参数无效；请使用 bizguard check --diff FILE。"))
        return 2
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
