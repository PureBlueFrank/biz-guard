"""Single application service that turns a change into one canonical decision."""

from __future__ import annotations

from fnmatch import fnmatchcase
from hashlib import sha256
import json
from pathlib import Path
import time
from threading import RLock

from bizguard.change.models import ChangeDecision, EvaluationRequest
from bizguard.diff_reconstruct import ReconstructionError, reconstruct_file
from bizguard.decision.legacy import FindingStatus, evaluate_parsed
from bizguard.decision.v2 import DecisionInput, DecisionState, FindingV2, NextAction, decide
from bizguard.diff_parser import DiffParseError, ParsedDiff, ParsedFile, parse_unified
from bizguard.eval.impact import changed_id_from_diff_text
from bizguard.graph.indexer import content_digest, index
from bizguard.graph.models import GraphSnapshot
from bizguard.impact.service import ImpactService
from bizguard.knowledge.ingest import knowledge_content_digest
from bizguard.observability import AuditTrail
from bizguard.policy.registry import PolicyDefinition, load_registry
from bizguard.policy.lifecycle import PolicyMode
from bizguard.policy.validators import validate_artifact
from bizguard.production import GovernancePaths
from bizguard.rag.injector import load_contract_registry
from bizguard.semantic.models import CatalogRequiredTest, SemanticCatalog, load_catalog
from bizguard.workflow.approval import ApprovalRequest
from bizguard.workflow.state_machine import ApprovalState
from bizguard.workflow.store import ApprovalStore

_PUBLIC_CONTRACT_SUFFIXES = {".proto", ".yaml", ".yml", ".json"}

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
        approval_store: ApprovalStore | None = None,
        audit: AuditTrail | None = None,
        metric_records: list[dict[str, object]] | None = None,
        governance: GovernancePaths | None = None,
    ) -> None:
        self._root = repository_root
        self._governance = governance or GovernancePaths.from_env()
        self._catalog = catalog or load_catalog(self._governance.catalog)
        self._registry = registry or load_registry(self._governance.policy_registry)
        self._contracts = load_contract_registry(self._governance.contract_registry)
        self._approval_store = approval_store
        self.audit = audit or AuditTrail()
        self.metric_records = metric_records if metric_records is not None else []
        self._graph_cache: dict[tuple[Path, str], GraphSnapshot] = {}
        self._graph_lock = RLock()

    def evaluate(self, request: EvaluationRequest) -> ChangeDecision:
        """Evaluate and emit metadata-only audit and latency records."""
        started = time.perf_counter()
        decision = self._evaluate(request)
        duration_ms = (time.perf_counter() - started) * 1000
        context_id = request.change_context_id or f"unscoped-{decision.base_revisions_sha256[:12]}"
        self.audit.add(
            "change_evaluated",
            context_id,
            trace_id=request.trace_id,
            decision=decision.decision.value,
            policy_revision=request.policy_revision,
            shadow_findings=str(len(decision.shadow_findings)),
        )
        self.metric_records.append(
            {
                "decision": decision.decision.value,
                "duration_ms": duration_ms,
                "unknown": any(item.critical_unknown for item in decision.findings),
                "shadow_findings": len(decision.shadow_findings),
                "trace_id": request.trace_id,
            }
        )
        return decision

    def _evaluate(self, request: EvaluationRequest) -> ChangeDecision:
        """Evaluate all changed files and return the canonical decision."""
        trace_id = request.trace_id
        revision_hash = sha256(
            json.dumps(request.base_revisions, sort_keys=True).encode("utf-8")
        ).hexdigest()
        revision = index_revision(request.base_revisions)
        current_repository_digest = content_digest(request.repository_root)
        knowledge_digest = knowledge_content_digest(self._governance.knowledge)

        try:
            parsed = parse_unified(request.diff_text)
        except DiffParseError as exc:
            return self._conservative(
                request,
                str(exc),
                revision_hash,
                current_repository_digest,
                knowledge_digest,
                trace_id,
            )

        baseline_repository_digest, target_repository_digest = _change_content_digests(
            parsed,
            request.repository_root,
            current_repository_digest,
        )

        findings: list[FindingV2] = []
        tests_by_id: dict[str, CatalogRequiredTest] = {}
        owners: set[str] = set()

        if (
            request.prepared_graph_content_digest is not None
            and baseline_repository_digest is not None
            and request.prepared_graph_content_digest != baseline_repository_digest
        ) or (
            request.prepared_knowledge_content_digest is not None
            and request.prepared_knowledge_content_digest != knowledge_digest
        ):
            findings.append(
                FindingV2(
                    id="context:STALE_CONTEXT",
                    severity="high",
                    effect="prepared context sources no longer match current governed content",
                    remediation="run prepare_change again against current repository and knowledge content",
                    confidence=1.0,
                    critical_unknown=True,
                )
            )

        python_files = [file for file in parsed.files if _is_python(file)]
        if python_files:
            findings.extend(self._python_findings(ParsedDiff(files=python_files)))
            for test in self._python_required_tests(python_files):
                tests_by_id[test.id] = test

        for file in parsed.files:
            file_findings, file_tests = self._validate_file(file, revision_hash)
            findings.extend(file_findings)
            for test in file_tests:
                tests_by_id[test.id] = test
            for finding in file_findings:
                if finding.required_approver and finding.policy_mode not in {
                    PolicyMode.DRAFT,
                    PolicyMode.SHADOW,
                }:
                    owners.add(finding.required_approver)

        impact_finding, impact_tests, impact_owners = self._impact_boundary(request, revision)
        if impact_finding is not None:
            findings.append(impact_finding)
        for test in impact_tests:
            tests_by_id[test.id] = test
        owners.update(impact_owners)

        prepared_tests = request.prepared_required_tests
        prepared_approvers = request.prepared_required_approvers
        unavailable_prepared_tests: list[str] = []
        if prepared_tests is not None:
            catalog_tests = {test.id: test for test in self._catalog.required_tests}
            for test_id in prepared_tests:
                prepared_test = catalog_tests.get(test_id)
                if prepared_test is None:
                    unavailable_prepared_tests.append(test_id)
                else:
                    tests_by_id[test_id] = prepared_test
        if prepared_approvers is not None:
            owners.update(prepared_approvers)

        required_test_ids = sorted(tests_by_id)
        if unavailable_prepared_tests:
            findings.append(
                FindingV2(
                    id="context:CONTEXT_DRIFT",
                    severity="high",
                    effect="prepared context references governed tests that are no longer available",
                    remediation="run prepare_change again and evaluate with the new context",
                    confidence=1.0,
                    critical_unknown=True,
                )
            )
        elif prepared_tests is not None or prepared_approvers is not None:
            added_tests = sorted(set(required_test_ids) - set(prepared_tests or []))
            added_approvers = sorted(owners - set(prepared_approvers or []))
            if added_tests or added_approvers:
                findings.append(
                    FindingV2(
                        id="context:CONTEXT_EXPANDED",
                        severity="medium",
                        effect="evaluation added governed tests or approvers without dropping prepared requirements",
                        remediation="follow the expanded requirements returned by this decision",
                        confidence=1.0,
                    )
                )
        tests_passed = self._tests_passed(request, required_test_ids)
        initial = decide(
            DecisionInput(
                findings=findings,
                required_tests=required_test_ids,
                tests_passed=tests_passed,
                owners=sorted(owners),
            )
        )
        approval_state, approval_satisfied = self._approval(
            request,
            initial.required_approvers,
            _decision_fingerprint(
                request,
                revision_hash,
                target_repository_digest,
                knowledge_digest,
                required_test_ids,
                initial.required_approvers,
            ),
        )
        result = decide(
            DecisionInput(
                findings=findings,
                required_tests=required_test_ids,
                tests_passed=tests_passed,
                owners=sorted(owners),
                approval_satisfied=approval_satisfied,
            )
        )

        fingerprint = _decision_fingerprint(
            request,
            revision_hash,
            target_repository_digest,
            knowledge_digest,
            required_test_ids,
            initial.required_approvers,
        )
        next_actions = _next_actions(
            result.decision,
            request,
            fingerprint,
            result.rationale,
            result.required_approvers,
            required_test_ids,
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
            decision_fingerprint=fingerprint,
            approval_state=approval_state,
            trace_id=trace_id,
            shadow_findings=result.shadow_findings,
            next_actions=next_actions,
        )

    def _python_required_tests(self, files: list[ParsedFile]) -> list[CatalogRequiredTest]:
        """Select governed tests for protected Python contract sources."""
        paths = {path for file in files for path in (file.old_path, file.new_path) if path}
        capabilities = {
            contract.capability for contract in self._contracts if contract.source in paths
        }
        return [test for test in self._catalog.required_tests if test.capability in capabilities]

    @staticmethod
    def _tests_passed(request: EvaluationRequest, required_test_ids: list[str]) -> bool | None:
        """Resolve test state without treating missing evidence as success."""
        if not required_test_ids:
            return True
        if request.tests_passed is not None:
            return request.tests_passed
        evidence = {item.test_id: item for item in request.test_evidence}
        if not all(test_id in evidence for test_id in required_test_ids):
            return None
        revision = index_revision(request.base_revisions)
        return all(
            evidence[test_id].passed and evidence[test_id].revision == revision
            for test_id in required_test_ids
        )

    def _approval(
        self,
        request: EvaluationRequest,
        required_approvers: list[str],
        decision_fingerprint: str,
    ) -> tuple[str | None, bool]:
        """Return only a revision- and owner-matched persisted approval."""
        if self._approval_store is None or request.change_context_id is None:
            return None, False
        payload = self._approval_store.get_by_context(
            request.change_context_id,
            request.policy_revision,
        )
        if payload is None:
            return None, False
        approval = ApprovalRequest.model_validate_json(payload)
        if approval.decision_fingerprint != decision_fingerprint:
            return "fingerprint_mismatch", False
        if not set(required_approvers).issubset(approval.approvers):
            return "approver_mismatch", False
        if approval.state is ApprovalState.APPROVED and not set(required_approvers).issubset(
            approval.approvals
        ):
            return "approver_mismatch", False
        if approval.state is ApprovalState.APPROVED and len(approval.approvals) < approval.required_cosigns:
            return "cosign_mismatch", False
        if approval.waiver is not None:
            if approval.waiver.active():
                return "waived", True
            if approval.state is not ApprovalState.APPROVED:
                return "expired", False
        return approval.state.value, approval.state is ApprovalState.APPROVED

    def _conservative(
        self,
        request: EvaluationRequest,
        message: str,
        revision_hash: str,
        repository_digest: str,
        knowledge_digest: str,
        trace_id: str | None,
    ) -> ChangeDecision:
        """Return a non-ALLOW decision for input that cannot be safely parsed."""
        fingerprint = _decision_fingerprint(
            request, revision_hash, repository_digest, knowledge_digest, [], []
        )
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
            decision_fingerprint=fingerprint,
            trace_id=trace_id,
            next_actions=_next_actions(
                DecisionState.REQUIRE_APPROVAL,
                request,
                fingerprint,
                "diff cannot be safely parsed",
                [],
                [],
            ),
        )

    def _python_findings(self, parsed_diff: ParsedDiff) -> list[FindingV2]:
        """Map the Python invariant pipeline's findings onto the shared finding model."""
        card = evaluate_parsed(
            parsed_diff,
            contract_registry_path=self._governance.contract_registry,
            invariants_path=self._governance.invariants,
            knowledge_root=self._governance.invariant_knowledge,
        )
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
                    policy_mode=self._policy_mode(item.finding_id),
                )
            )
        return findings

    def _policy_mode(self, policy_id: str) -> PolicyMode:
        policy = next((item for item in self._registry if item.id == policy_id), None)
        return policy.mode if policy is not None else PolicyMode.BLOCKING

    def _validate_file(
        self, file: ParsedFile, revision_hash: str
    ) -> tuple[list[FindingV2], list[CatalogRequiredTest]]:
        """Validate every governed side of a file change, including type downgrades."""
        old_policies = {
            policy.id: policy for policy in self._artifact_policies(file.old_path)
        } if file.old_path else {}
        new_policies = {
            policy.id: policy for policy in self._artifact_policies(file.new_path)
        } if file.new_path else {}
        policies = old_policies | new_policies
        if not policies:
            return [], []
        try:
            reconstructed = reconstruct_file(file, self._root)
        except (OSError, UnicodeError, ReconstructionError) as exc:
            return [
                FindingV2(
                    id=f"{policy.id}:RECONSTRUCTION_INCOMPLETE:{revision_hash[:12]}",
                    severity="high",
                    effect=f"DEGRADED: base mismatch, reconstruction unavailable: {exc}",
                    remediation="evaluate the diff against its exact before or after state",
                    confidence=1.0,
                    critical_unknown=True,
                    required_approver=policy.owner,
                    policy_mode=policy.mode,
                    validator_precision=policy.precision,
                )
                for policy in policies.values()
            ], []

        validations: list[tuple[PolicyDefinition, str, str, str | None]] = []
        for policy_id in sorted(policies):
            old_policy = old_policies.get(policy_id)
            new_policy = new_policies.get(policy_id)
            same_artifact_kind = (
                old_policy is not None
                and new_policy is not None
                and file.old_path is not None
                and file.new_path is not None
                and _artifact_kind(file.old_path) == _artifact_kind(file.new_path)
            )
            if old_policy is not None and not same_artifact_kind:
                validations.append(
                    (old_policy, file.old_path or "", "", reconstructed.before)
                )
            if new_policy is not None:
                baseline = reconstructed.before if same_artifact_kind else None
                validations.append(
                    (
                        new_policy,
                        file.new_path or file.old_path or "",
                        reconstructed.after,
                        baseline,
                    )
                )
            elif old_policy is not None and same_artifact_kind:
                validations.append(
                    (old_policy, file.old_path or "", reconstructed.after, reconstructed.before)
                )

        public_change = any(
            path is not None and Path(path).suffix.lower() in _PUBLIC_CONTRACT_SUFFIXES
            for path in (file.old_path, file.new_path)
        ) or any(
            token in "\n".join(file.added_lines).lower()
            for token in ("openapi", "message ", "dto", "enum ", "record ")
        )
        findings: list[FindingV2] = []
        required_test_ids: set[str] = set()
        for policy, path, source, baseline in validations:
            artifact = validate_artifact(
                policy.id,
                source,
                path,
                severity=policy.severity,
                baseline_source=baseline,
                validator=policy.validator,
            )
            confidence = artifact["confidence"]
            if not isinstance(confidence, (int, float)):
                raise ValueError("policy validator returned a non-numeric confidence")
            findings.append(
                FindingV2(
                    id=f"{artifact['id']}:{revision_hash[:12]}",
                    severity=str(artifact["severity"]),
                    effect=str(artifact["effect"]),
                    remediation=str(artifact["remediation"]),
                    confidence=float(confidence),
                    violated=bool(artifact["violated"]),
                    public_contract=public_change,
                    required_approver=policy.owner,
                    policy_mode=policy.mode,
                    validator_precision=policy.precision,
                )
            )
            if policy.mode not in {PolicyMode.DRAFT, PolicyMode.SHADOW}:
                required_test_ids.update(policy.required_tests)
        tests = [
            test for test in self._catalog.required_tests if test.id in required_test_ids
        ]
        return findings, tests

    def _artifact_policies(self, path: str) -> list[PolicyDefinition]:
        """Select every policy whose organization-owned pattern matches the artifact."""
        normalized = path.replace("\\", "/")
        return [
            policy
            for policy in self._registry
            if any(fnmatchcase(normalized, pattern) for pattern in policy.file_patterns)
        ]

    def _impact_boundary(
        self, request: EvaluationRequest, revision: str
    ) -> tuple[FindingV2 | None, list[CatalogRequiredTest], list[str]]:
        """Return a conservative finding when the impact path ends at an unknown boundary."""
        key = (request.repository_root.resolve(), revision)
        digest = content_digest(request.repository_root)
        with self._graph_lock:
            snapshot = self._graph_cache.get(key)
            if snapshot is None or snapshot.content_digest != digest:
                snapshot = index(request.repository_root, revision, self._catalog)
                self._graph_cache[key] = snapshot
        try:
            changed_symbol = changed_id_from_diff_text(snapshot, request.diff_text, "change")
        except ValueError:
            return None, [], []
        try:
            report = ImpactService(request.repository_root, self._catalog).analyze(
                changed_symbol,
                revision,
                capability=None,
                diff_text=request.diff_text,
                snapshot=snapshot,
            )
        except ValueError as exc:
            finding = FindingV2(
                id=f"impact:CAPABILITY_UNRESOLVED:{changed_symbol}",
                severity="high",
                effect=str(exc),
                remediation="register an unambiguous capability and required-test mapping",
                confidence=1.0,
                critical_unknown=True,
            )
            return finding, [], []
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


def _change_content_digests(
    parsed: ParsedDiff,
    repository_root: Path,
    current_digest: str,
) -> tuple[str | None, str]:
    """Return virtual before/after graph digests independent of worktree diff state."""
    before_overrides: dict[str, bytes | None] = {}
    after_overrides: dict[str, bytes | None] = {}
    for file in parsed.files:
        old_path = _graph_relative_path(file.old_path, repository_root)
        new_path = _graph_relative_path(file.new_path, repository_root)
        if old_path is None and new_path is None:
            continue
        try:
            reconstructed = reconstruct_file(file, repository_root)
        except (OSError, UnicodeError, ReconstructionError):
            return None, current_digest
        if old_path is not None:
            before_overrides[old_path] = reconstructed.before.encode("utf-8")
            after_overrides[old_path] = (
                reconstructed.after.encode("utf-8")
                if new_path == old_path
                else None
            )
        if new_path is not None and new_path != old_path:
            before_overrides[new_path] = None
            after_overrides[new_path] = reconstructed.after.encode("utf-8")
    return (
        content_digest(repository_root, content_overrides=before_overrides),
        content_digest(repository_root, content_overrides=after_overrides),
    )


def _graph_relative_path(path: str | None, repository_root: Path) -> str | None:
    """Normalize a diff path only when it is consumed by the graph indexer."""
    if path is None:
        return None
    relative = Path(path)
    if repository_root.name in relative.parts:
        marker = relative.parts.index(repository_root.name)
        relative = Path(*relative.parts[marker + 1 :])
    else:
        repository_names = {
            child.name for child in repository_root.iterdir() if child.is_dir()
        }
        relative = next(
            (
                Path(*relative.parts[marker:])
                for marker, part in enumerate(relative.parts)
                if part in repository_names
            ),
            relative,
        )
    normalized = relative.as_posix()
    if relative.suffix.lower() not in {".java", ".yaml", ".proto"} and normalized != (
        "bizguard-manual-edges.yaml"
    ):
        return None
    return normalized


def _is_python(file: ParsedFile) -> bool:
    return any(
        path is not None and path.endswith(".py")
        for path in (file.old_path, file.new_path)
    )


def _artifact_kind(path: str) -> str:
    """Group only suffixes whose validator can compare the same artifact format."""
    suffix = Path(path).suffix.lower()
    if suffix in {".yaml", ".yml", ".json"}:
        return "openapi"
    if suffix in {".avsc", ".schema"}:
        return "message-schema"
    if suffix in {".properties", ".conf", ".env"}:
        return "configuration"
    return suffix


def _decision_fingerprint(
    request: EvaluationRequest,
    revision_hash: str,
    repository_content_digest: str,
    knowledge_digest: str,
    required_tests: list[str],
    required_approvers: list[str],
) -> str:
    """Bind approval to the diff, baseline, repository content, policy, tests, and owners."""
    payload = {
        "diff_sha256": sha256(request.diff_text.encode("utf-8")).hexdigest(),
        "base_revisions_sha256": revision_hash,
        "repository_content_digest": repository_content_digest,
        "knowledge_content_digest": knowledge_digest,
        "policy_revision": request.policy_revision,
        "required_tests": sorted(required_tests),
        "required_approvers": sorted(required_approvers),
    }
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def index_revision(base_revisions: dict[str, object]) -> str:
    """Use the prepared index revision, or derive one from explicit repository revisions."""
    explicit = base_revisions.get("__index__") or base_revisions.get("revision")
    if explicit is not None:
        return str(explicit)
    if not base_revisions:
        return "phase3-fixture-v1"
    return sha256(json.dumps(base_revisions, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _next_actions(
    state: DecisionState,
    request: EvaluationRequest,
    fingerprint: str,
    reason: str,
    required_approvers: list[str],
    required_tests: list[str],
) -> list[NextAction]:
    """Translate a terminal decision into an explicit agent follow-up."""
    if state is DecisionState.REQUIRE_APPROVAL:
        if request.change_context_id is None:
            prepare_inputs = _prepare_change_inputs(request)
            if prepare_inputs is None:
                return [
                    NextAction(
                        tool="none",
                        reason="provide a valid unified diff before preparing a change context",
                        inputs={},
                    )
                ]
            return [
                NextAction(
                    tool="prepare_change",
                    reason="an approval must be bound to a persisted change context",
                    inputs=prepare_inputs,
                )
            ]
        if not required_approvers:
            return [
                NextAction(
                    tool="none",
                    reason="register a governed approver before creating an approval request",
                    inputs={},
                )
            ]
        return [
            NextAction(
                tool="request_approval",
                reason=reason,
                inputs={
                    "change_context_id": request.change_context_id,
                    "decision_fingerprint": fingerprint,
                    "policy_revision": request.policy_revision,
                    "approvers": required_approvers,
                    "required_cosigns": 1,
                },
            )
        ]
    if state is DecisionState.ALLOW_WITH_TESTS:
        return [
            NextAction(
                tool="none",
                reason="trusted CI must execute and attest the required tests",
                inputs={"required_tests": required_tests},
            )
        ]
    if state is DecisionState.BLOCK:
        return [NextAction(tool="none", reason="resolve blocking findings before retrying", inputs={})]
    return []


def _prepare_change_inputs(request: EvaluationRequest) -> dict[str, object] | None:
    """Derive a complete, deterministic prepare_change call from an evaluated diff."""
    try:
        parsed = parse_unified(request.diff_text)
    except DiffParseError:
        return None
    repository_names = {
        child.name for child in request.repository_root.iterdir() if child.is_dir()
    }
    changed_paths = sorted(
        path
        for file in parsed.files
        if (path := file.new_path or file.old_path) is not None
    )
    repos = sorted(
        {
            part
            for path in changed_paths
            for part in Path(path).parts
            if part in repository_names
        }
    )
    if not repos:
        repos = sorted(repository_names)[:1]
    revisions = _normalized_revisions(request.base_revisions)
    revision = index_revision(revisions)
    for repo in repos:
        revisions.setdefault(repo, revision)
    revisions.setdefault("__index__", revision)
    return {
        "task": "Review changes to " + ", ".join(changed_paths),
        "repos": repos,
        "base_revisions": revisions,
    }


def _normalized_revisions(base_revisions: dict[str, object]) -> dict[str, object]:
    """Flatten revision-set files into the mapping accepted by prepare_change."""
    repositories = base_revisions.get("repositories")
    if isinstance(repositories, dict):
        revisions: dict[str, object] = {
            str(key): str(value) for key, value in repositories.items()
        }
        explicit = base_revisions.get("revision") or base_revisions.get("__index__")
        if explicit is not None:
            revisions["__index__"] = str(explicit)
        return revisions
    return dict(base_revisions)
