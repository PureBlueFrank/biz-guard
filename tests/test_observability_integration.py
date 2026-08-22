"""Observability integration: audit replay, redaction, and metric aggregation."""

from __future__ import annotations

import json
from pathlib import Path

from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import EvaluationRequest
from bizguard.observability import AuditTrail, export_metrics
from bizguard.workflow.approval import ApprovalRequest, ApprovalService
from bizguard.workflow.store import SqliteApprovalStore


ROOT = Path(__file__).parents[1]
REPOSITORY_ROOT = ROOT / "fixtures" / "java-microservices"


def test_audit_events_reconstruct_in_order_by_context(tmp_path: Path) -> None:
    store = SqliteApprovalStore(tmp_path / "approvals.db")
    service = ApprovalService(store=store)
    request = service.create(
        ApprovalRequest(change_context_id="ctx", policy_revision="r", approvers=("a",), required_cosigns=1)
    )
    service.request_evidence(request, "need test")
    service.add_evidence(request, "test://run")
    service.approve(request, "a")
    store.close()

    reopened = SqliteApprovalStore(tmp_path / "approvals.db")
    actions = [json.loads(event)["action"] for event in reopened.events("ctx")]
    assert actions == [
        "approval_created",
        "evidence_requested",
        "evidence_added",
        "approval_recorded",
        "approval_granted",
    ]
    reopened.close()


def test_redaction_covers_field_names_and_inline_values() -> None:
    trail = AuditTrail()
    trail.add(
        "probe",
        "ctx",
        api_key="sk-secret123",
        note="password=secretvalue",
        conversation="secret chat",
        token="Bearer eyJhbGciOiJIUzI1NiJ9.abcdefghijk",
    )
    details = trail.events_for("ctx")[0].details
    assert details["api_key"] == "[REDACTED]"
    assert details["conversation"] == "[REDACTED]"
    assert "secretvalue" not in details["note"]
    assert "eyJ" not in details["token"]


def test_metrics_export_percentiles_distribution_and_unknown_rate() -> None:
    records = [
        {"decision": "ALLOW", "duration_ms": 10.0, "unknown": False},
        {"decision": "BLOCK", "duration_ms": 20.0, "unknown": True},
        {"decision": "BLOCK", "duration_ms": 30.0, "unknown": False},
    ]
    metrics = export_metrics(records)
    assert metrics["count"] == 3.0
    assert metrics["decision_distribution"] == {"ALLOW": 1, "BLOCK": 2}
    assert abs(float(metrics["unknown_rate"]) - 1 / 3) < 1e-9  # type: ignore[arg-type]
    assert metrics["duration_ms_p50"] == 20.0
    assert metrics["duration_ms_p95"] == 29.0
    assert metrics["low_sample"] is True


def test_evaluator_propagates_provided_trace_id() -> None:
    diff = "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1,1 +1,1 @@\n-a\n+b\n"
    decision = ChangeEvaluator(REPOSITORY_ROOT).evaluate(
        EvaluationRequest(diff_text=diff, repository_root=REPOSITORY_ROOT, trace_id="trace-42")
    )
    assert decision.trace_id == "trace-42"


def test_evaluator_leaves_trace_id_none_when_absent() -> None:
    diff = "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1,1 +1,1 @@\n-a\n+b\n"
    decision = ChangeEvaluator(REPOSITORY_ROOT).evaluate(
        EvaluationRequest(diff_text=diff, repository_root=REPOSITORY_ROOT)
    )
    assert decision.trace_id is None
