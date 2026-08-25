"""PostgreSQL integration checks for multi-instance production persistence."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from bizguard.change.store import PostgresChangeContextStore
from bizguard.workflow.approval import ApprovalRequest, ApprovalService
from bizguard.workflow.state_machine import ApprovalState
from bizguard.workflow.store import PostgresApprovalStore


DATABASE_URL = os.environ.get("BIZGUARD_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="test PostgreSQL is unavailable")


def test_postgres_context_store_is_immutable_across_instances() -> None:
    assert DATABASE_URL is not None
    first = PostgresChangeContextStore(DATABASE_URL, min_pool_size=0, max_pool_size=2)
    second = PostgresChangeContextStore(DATABASE_URL, min_pool_size=0, max_pool_size=2)
    context_id = f"ctx-{uuid4()}"
    try:
        first.put(context_id, '{"value":1}', "2026-08-25T00:00:00+00:00")
        second.put(context_id, '{"value":1}', "2026-08-25T00:00:00+00:00")
        assert second.get(context_id) == '{"value":1}'
        with pytest.raises(ValueError, match="immutable"):
            second.put(context_id, '{"value":2}', "2026-08-25T00:00:01+00:00")
        assert first.ping() and second.ping()
    finally:
        first.close()
        second.close()


def test_postgres_approval_updates_merge_stale_cross_instance_requests() -> None:
    assert DATABASE_URL is not None
    first = PostgresApprovalStore(DATABASE_URL, min_pool_size=0, max_pool_size=2)
    second = PostgresApprovalStore(DATABASE_URL, min_pool_size=0, max_pool_size=2)
    context_id = f"ctx-{uuid4()}"
    request = ApprovalService(first).create(
        ApprovalRequest(
            change_context_id=context_id,
            policy_revision="production-v1",
            decision_fingerprint="a" * 64,
            approvers=("owner-a", "owner-b"),
            required_cosigns=2,
        )
    )
    stale_copy = request.model_copy(deep=True)
    try:
        ApprovalService(first).approve(request, "owner-a")
        ApprovalService(second).approve(stale_copy, "owner-b")
        restored = ApprovalService(first).create(request.model_copy(deep=True))
        assert restored.approvals == {"owner-a", "owner-b"}
        assert restored.state is ApprovalState.APPROVED
        assert len(first.events(context_id)) == 4
        assert first.ping() and second.ping()
    finally:
        first.close()
        second.close()
