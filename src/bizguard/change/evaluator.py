"""Single application service that turns a change into one canonical decision."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from bizguard.change.models import ChangeDecision, EvaluationRequest
from bizguard.decision.legacy import FindingStatus, evaluate_parsed
from bizguard.decision.v2 import DecisionInput, DecisionState, FindingV2, decide
from bizguard.diff_parser import DiffParseError, ParsedDiff, ParsedFile, parse_unified
from bizguard.eval.impact import changed_id_from_diff_text
from bizguard.graph.indexer import index
from bizguard.impact.service import ImpactService
from bizguard.policy.registry import PolicyDefinition, load_registry
from bizguard.policy.validators import validate_artifact
from bizguard.semantic.models import CatalogRequiredTest, SemanticCatalog, load_catalog

_PUBLIC_CONTRACT_SUFFIXES = {".proto", ".yaml", ".yml", ".json"}

_ARTIFACT_POLICY_BY_SUFFIX = {
    ".proto": "published-dto-backward-compatible",
    ".yaml": "published-dto-backward-compatible",
    ".yml": "published-dto-backward-compatible",
    ".json": "published-dto-backward-compatible",
    ".sql": "redeem-ledger-consistency",
    ".avsc": "coupon-write-consumes-idempotency-key",
    ".schema": "coupon-write-consumes-idempotency-key",
    ".properties": "published-dto-backward-compatible",
    ".conf": "published-dto-backward-compatible",
    ".env": "published-dto-backward-compatible",
}


class ChangeEvaluator:
    """Evaluate a multi-file diff into one canonical four-state decision.

    This is the only application service that aggregates policy validation,
    impact analysis and required tests into a :class:`ChangeDecision`.  Entry
    points (CLI, MCP, Hook, CI) must delegate here rather than re-implementing
    the aggregation.
    """

    def __init__(
        self,
        repository_root: Path,
        *,
        catalog: SemanticCatalog | None = None,
        registry: list[PolicyDefinition] | None = None,
    ) -> None:
        self._root = repository_root
        project_root = Path(__file__).parents[3]
        self._catalog = catalog or load_catalog(Path(__file__).parents[1] / "semantic" / "catalog.yaml")
        self._registry = registry or load_registry(project_root / "policy" / "phase5-registry.yaml")

    def evaluate(self, request: EvaluationRequest) -> ChangeDecision:
        """Evaluate all changed files and return the canonical decision."""
        trace_id = request.trace_id
        revision_hash = sha256(
            json.dumps(request.base_revisions, sort_keys=True).encode("utf-8")
        ).hexdigest()
        revision = str(request.base_revisions.get("revision", "phase3-fixture-v1"))

        try:
            parsed = parse_unified(request.diff_text)
        except DiffParseError as exc:
            return self._conservative(request, str(exc), revision_hash, trace_id)

        findings: list[FindingV2] = []
        tests_by_id: dict[str, CatalogRequiredTest] = {}
        owners: set[str] = set()

        python_files = [file for file in parsed.files if _is_python(file)]
        if python_files:
            findings.extend(self._python_findings(ParsedDiff(files=python_files)))

        for file in parsed.files:
            file_findings, file_tests = self._validate_file(file, revision_hash)
            findings.extend(file_findings)
            for test in file_tests:
                tests_by_id[test.id] = test
            for finding in file_findings:
                if finding.required_approver:
                    owners.add(finding.required_approver)

        impact_finding, impact_tests, impact_owners = self._impact_boundary(request, revision)
        if impact_finding is not None:
            findings.append(impact_finding)
        for test in impact_tests:
            tests_by_id[test.id] = test
        owners.update(impact_owners)

        required_test_ids = sorted(tests_by_id)
        result = decide(
            DecisionInput(
                findings=findings,
                required_tests=required_test_ids,
                tests_passed=request.tests_passed,
                owners=sorted(owners),
            )
        )

        return ChangeDecision(
            decision=result.decision,
            rationale=result.rationale,
            findings=findings,
            required_tests=[tests_by_id[key] for key in required_test_ids],
            required_approvers=result.required_approvers,
            evidence=result.evidence,
            risk_score=result.risk_score,
            change_context_id=request.change_context_id,
            policy_revision=request.policy_revision,
            base_revisions_sha256=revision_hash,
            trace_id=trace_id,
        )

    def _conservative(self, request: EvaluationRequest, message: str, revision_hash: str, trace_id: str | None) -> ChangeDecision:
        """Return a non-ALLOW decision for input that cannot be safely parsed."""
        return ChangeDecision(
            decision=DecisionState.REQUIRE_APPROVAL,
            rationale="diff 无法安全解析，需人工确认",
            findings=[
                FindingV2(
                    id="fault:diff_parse",
                    severity="high",
                    effect=message,
                    remediation="提供完整、非二进制的 unified diff",
                    confidence=1.0,
                    critical_unknown=True,
                )
            ],
            risk_score=0.0,
            change_context_id=request.change_context_id,
            policy_revision=request.policy_revision,
            base_revisions_sha256=revision_hash,
            trace_id=trace_id,
        )

    def _python_findings(self, parsed_diff: ParsedDiff) -> list[FindingV2]:
        """Map the Python invariant pipeline's findings onto the shared finding model."""
        card = evaluate_parsed(parsed_diff)
        findings: list[FindingV2] = []
        for item in card.findings:
            if item.status is FindingStatus.VIOLATED:
                severity = "critical"
            elif item.status is FindingStatus.INCOMPLETE:
                severity = "high"
            else:
                severity = "medium"
            findings.append(
                FindingV2(
                    id=item.finding_id,
                    severity=severity,
                    effect=item.message,
                    remediation="resolve the reported policy finding",
                    confidence=1.0,
                    violated=item.status is FindingStatus.VIOLATED,
                    critical_unknown=item.status is FindingStatus.INCOMPLETE,
                )
            )
        return findings

    def _validate_file(
        self, file: ParsedFile, revision_hash: str
    ) -> tuple[list[FindingV2], list[CatalogRequiredTest]]:
        """Validate one changed file with the policy selected by file type."""
        path = file.new_path or file.old_path
        if path is None:
            return [], []
        policy = self._artifact_policy(path)
        if policy is None:
            return [], []
        content = _file_content(file)
        artifact = validate_artifact(policy.id, content, path, severity=policy.severity)
        confidence = artifact["confidence"]
        if not isinstance(confidence, (int, float)):
            raise ValueError("policy validator returned a non-numeric confidence")
        public_change = Path(path).suffix.lower() in _PUBLIC_CONTRACT_SUFFIXES or any(
            token in "\n".join(file.added_lines).lower()
            for token in ("openapi", "message ", "dto", "enum ", "record ")
        )
        finding = FindingV2(
            id=f"{artifact['id']}:{revision_hash[:12]}",
            severity=str(artifact["severity"]),
            effect=str(artifact["effect"]),
            remediation=str(artifact["remediation"]),
            confidence=float(confidence),
            violated=bool(artifact["violated"]),
            public_contract=public_change,
            required_approver=policy.owner,
        )
        tests = [test for test in self._catalog.required_tests if test.id in policy.required_tests]
        return [finding], tests

    def _artifact_policy(self, path: str) -> PolicyDefinition | None:
        """Select the artifact policy by file suffix, looked up from the registry."""
        policy_id = _ARTIFACT_POLICY_BY_SUFFIX.get(Path(path).suffix.lower())
        if policy_id is None:
            return None
        return next((item for item in self._registry if item.id == policy_id), None)

    def _impact_boundary(
        self, request: EvaluationRequest, revision: str
    ) -> tuple[FindingV2 | None, list[CatalogRequiredTest], list[str]]:
        """Return a conservative finding when the impact path ends at an unknown boundary."""
        snapshot = index(request.repository_root, revision)
        try:
            changed_symbol = changed_id_from_diff_text(snapshot, request.diff_text, "change")
        except ValueError:
            return None, [], []
        report = ImpactService(request.repository_root).analyze(
            changed_symbol,
            revision,
            capability=None,
            diff_text=request.diff_text,
        )
        tests = [CatalogRequiredTest.model_validate(item) for item in report.required_tests]
        if not report.unknown_boundary:
            return None, tests, []
        reason = report.unknown_reason or "UNKNOWN_BOUNDARY"
        approver = report.required_approvers[0] if report.required_approvers else None
        finding = FindingV2(
            id=f"impact:{reason}:{changed_symbol}",
            severity="high",
            effect="cross-service impact path ends at an unknown boundary",
            remediation="obtain owner approval and attach boundary evidence",
            required_approver=approver,
            confidence=1.0,
            critical_unknown=True,
        )
        return finding, tests, report.required_approvers


def _file_content(file: ParsedFile) -> str:
    """Reconstruct a file's post-change content from context and added lines."""
    lines: list[str] = []
    for hunk in file.hunks:
        for line in hunk.lines:
            if line.startswith((" ", "+")):
                lines.append(line[1:])
    return "\n".join(lines)


def _is_python(file: ParsedFile) -> bool:
    path = file.new_path or file.old_path
    return path is not None and path.endswith(".py")
