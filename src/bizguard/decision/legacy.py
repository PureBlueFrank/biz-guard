"""Shared, ordered decision pipeline for every BizGuard entry point."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from bizguard.diff_parser import DiffParseError, ParsedDiff, parse
from bizguard.diff_reconstruct import ReconstructionError, reconstruct_file
from bizguard.policy.invariants import PolicyLoadError, load_invariants
from bizguard.rag.injector import load_contract_registry, load_knowledge_documents, inject_full_text


class Decision(StrEnum):
    """Possible safety outcomes for a submitted change."""

    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    CHECK_INCOMPLETE = "CHECK_INCOMPLETE"


class FindingStatus(StrEnum):
    """Completion status of a single policy finding."""

    PASSED = "passed"
    VIOLATED = "violated"
    INCOMPLETE = "incomplete"


class FaultCode(StrEnum):
    """Machine-readable causes for incomplete or degraded checks."""

    INPUT_VALIDATION = "input_validation"
    DIFF_PARSE = "diff_parse"
    POLICY_UNCOVERED = "policy_uncovered"
    RETRIEVAL_EMPTY = "retrieval_empty"
    EMBEDDING_TIMEOUT = "embedding_timeout"
    CACHE_CORRUPT = "cache_corrupt"
    MCP_DISCONNECTED = "mcp_disconnected"


class Fault(BaseModel):
    """Evidence retained for a non-successful check condition."""

    code: FaultCode
    message: str
    evidence_refs: list[str]


class Finding(BaseModel):
    """Evidence-backed result for one policy check."""

    finding_id: str
    status: FindingStatus
    message: str
    evidence_refs: list[str]


class ChangeSafetyCard(BaseModel):
    """The sole JSON-compatible output contract for BizGuard decisions."""

    decision: Decision
    findings: list[Finding]
    faults: list[Fault]


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
def evaluate_change(
    diff_text: str,
    *,
    contract_registry_path: Path | None = None,
    invariants_path: Path | None = None,
    knowledge_root: Path | None = None,
) -> ChangeSafetyCard:
    """Evaluate a diff in this fixed order: parse, coverage, retrieval, reconstruct, AST.

    Service ownership comes from each parsed file's non-null new path (or old
    path for a deletion); therefore a rename is owned by its ``rename to`` path.
    Policy coverage is resolved before retrieval, so ``policy_uncovered`` has
    higher priority than ``retrieval_empty`` when both could apply.
    """
    try:
        parsed_diff = parse(diff_text)
    except DiffParseError as exc:
        return _incomplete(FaultCode.DIFF_PARSE, str(exc))
    return evaluate_parsed(
        parsed_diff,
        contract_registry_path=contract_registry_path,
        invariants_path=invariants_path,
        knowledge_root=knowledge_root,
    )


def evaluate_parsed(
    parsed_diff: ParsedDiff,
    *,
    contract_registry_path: Path | None = None,
    invariants_path: Path | None = None,
    knowledge_root: Path | None = None,
) -> ChangeSafetyCard:
    """Evaluate an already-parsed diff through the Python invariant pipeline."""
    contract_path = contract_registry_path or _PROJECT_ROOT / "registry" / "contracts.yaml"
    invariant_path = invariants_path or _PROJECT_ROOT / "policy" / "invariants.yaml"
    documents_path = knowledge_root or _PROJECT_ROOT / "knowledge"
    try:
        registry = load_contract_registry(contract_path)
        invariants = load_invariants(invariant_path, contract_path, documents_path)
    except (OSError, PolicyLoadError, ValueError) as exc:
        return _incomplete(FaultCode.POLICY_UNCOVERED, f"无法加载 Policy 契约: {exc}")

    changed_paths = {_service_path(file) for file in parsed_diff.files}
    covered_policy_ids = {
        policy_id
        for contract in registry
        if contract.source in changed_paths
        for policy_id in contract.policy_ids
    }
    if not covered_policy_ids:
        return _incomplete(
            FaultCode.POLICY_UNCOVERED,
            "受支持服务的变更没有匹配到任何 Policy。",
            sorted(changed_paths),
        )

    try:
        documents = load_knowledge_documents(documents_path)
        evidence = inject_full_text(parsed_diff, registry, documents)
    except (OSError, ValueError) as exc:
        return _incomplete(FaultCode.RETRIEVAL_EMPTY, f"检索资料不可用: {exc}")
    if not evidence.contract_ids or not evidence.knowledge_document_ids:
        return _incomplete(FaultCode.RETRIEVAL_EMPTY, "没有找到匹配的契约或知识文档。")

    from bizguard.policy.validators import validate_invariant

    findings: list[Finding] = []
    faults: list[Fault] = []
    for invariant in invariants:
        if invariant.id not in covered_policy_ids or invariant.target.file not in changed_paths:
            continue
        try:
            full_text = _reconstruct_target(parsed_diff, invariant.target.file)
        except (OSError, UnicodeError, ReconstructionError, ValueError) as exc:
            message = f"无法应用 diff 重建变更后文本: {exc}"
            findings.append(
                Finding(
                    finding_id=invariant.id,
                    status=FindingStatus.INCOMPLETE,
                    message=message,
                    evidence_refs=[f"policy:{invariant.id}", invariant.target.file],
                )
            )
            faults.append(
                Fault(
                    code=FaultCode.DIFF_PARSE,
                    message=message,
                    evidence_refs=[invariant.target.file],
                )
            )
            continue
        findings.append(validate_invariant(parsed_diff, full_text, invariant))
    findings.sort(key=lambda finding: finding.finding_id)
    if any(finding.status is FindingStatus.INCOMPLETE for finding in findings):
        return ChangeSafetyCard(
            decision=Decision.CHECK_INCOMPLETE, findings=findings, faults=faults
        )
    if any(finding.status is FindingStatus.VIOLATED for finding in findings):
        return ChangeSafetyCard(decision=Decision.BLOCK, findings=findings, faults=[])
    return ChangeSafetyCard(decision=Decision.ALLOW, findings=findings, faults=[])


def _incomplete(code: FaultCode, message: str, refs: list[str] | None = None) -> ChangeSafetyCard:
    return ChangeSafetyCard(
        decision=Decision.CHECK_INCOMPLETE,
        findings=[
            Finding(
                finding_id=f"fault:{code}",
                status=FindingStatus.INCOMPLETE,
                message=message,
                evidence_refs=refs or [],
            )
        ],
        faults=[Fault(code=code, message=message, evidence_refs=refs or [])],
    )


def _service_path(parsed_file: object) -> str:
    path = getattr(parsed_file, "new_path") or getattr(parsed_file, "old_path")
    if not isinstance(path, str):
        raise ValueError("parsed file has no source path")
    return path


def _reconstruct_target(parsed_diff: ParsedDiff, target_path: str) -> str:
    """Use the canonical fail-closed reconstructor for the legacy adapter."""
    parsed_file = next(
        (file for file in parsed_diff.files if _service_path(file) == target_path), None
    )
    if parsed_file is None:
        raise ValueError("target file was not changed")
    return reconstruct_file(parsed_file, _PROJECT_ROOT).after
