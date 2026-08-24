"""Canonical ChangeEvaluator parity contract: one schema, one aggregation path."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from collections.abc import Mapping

import pytest

from agents_mcp.server import mcp
from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import ChangeDecision, EvaluationRequest
from bizguard.ci.check import evaluate as ci_evaluate
from bizguard.hooks.agent import validate as hook_validate


ROOT = Path(__file__).parents[1]
REPOSITORY_ROOT = ROOT / "fixtures" / "java-microservices"

CORE_FIELDS = [
    "decision",
    "rationale",
    "findings",
    "required_tests",
    "required_approvers",
    "evidence",
    "risk_score",
    "change_context_id",
    "policy_revision",
    "base_revisions_sha256",
]


def normalize_core_result(result: object) -> dict[str, object]:
    """Strip entry-point-specific and non-deterministic fields for parity comparison."""
    if isinstance(result, ChangeDecision):
        payload: Mapping[str, object] = result.model_dump(mode="json")
    elif isinstance(result, Mapping):
        payload = result
    else:
        raise TypeError(f"unexpected result type: {type(result).__name__}")
    return {key: payload[key] for key in CORE_FIELDS}


def _evaluate(
    diff_text: str,
    *,
    base_revisions: dict[str, object] | None = None,
    tests_passed: bool | None = True,
) -> ChangeDecision:
    return ChangeEvaluator(REPOSITORY_ROOT).evaluate(
        EvaluationRequest(
            diff_text=diff_text,
            repository_root=REPOSITORY_ROOT,
            base_revisions=base_revisions or {},
            tests_passed=tests_passed,
        )
    )


def _fixture(name: str) -> str:
    return (ROOT / "bench" / "fixtures" / "phase5" / name).read_text(encoding="utf-8")


SQL_NON_TRANSACTIONAL = """\
diff --git a/coupon-core/src/main/resources/db/V2__ledger.sql b/coupon-core/src/main/resources/db/V2__ledger.sql
--- a/coupon-core/src/main/resources/db/V2__ledger.sql
+++ b/coupon-core/src/main/resources/db/V2__ledger.sql
@@ -1,1 +1,1 @@
-UPDATE ledger SET status='SUCCESS';
+UPDATE ledger SET status='FAILED';
"""

AVSC_WITHOUT_VERSION = """\
diff --git a/coupon-core/src/main/avro/Coupon.avsc b/coupon-core/src/main/avro/Coupon.avsc
--- a/coupon-core/src/main/avro/Coupon.avsc
+++ b/coupon-core/src/main/avro/Coupon.avsc
@@ -1,2 +1,2 @@
 type: record
-name: Coupon
+name: CouponV2
"""

UNRELATED_MARKDOWN = """\
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,1 +1,1 @@
-# title
+# title v2
"""

UNRELATED_TEXT = """\
diff --git a/docs/notes.txt b/docs/notes.txt
--- a/docs/notes.txt
+++ b/docs/notes.txt
@@ -1,1 +1,1 @@
-hello
+world
"""


@pytest.mark.parametrize(
    ("diff_text", "tests_passed", "expected"),
    [
        (SQL_NON_TRANSACTIONAL, True, "ALLOW"),
        (AVSC_WITHOUT_VERSION, True, "ALLOW"),
        (_fixture("cross-service-dto-breaking.diff"), None, "REQUIRE_APPROVAL"),
        (_fixture("dynamic-mapper.diff"), None, "REQUIRE_APPROVAL"),
        (_fixture("cross-service-dto-breaking.diff"), True, "REQUIRE_APPROVAL"),
        (_fixture("dynamic-mapper.diff"), True, "REQUIRE_APPROVAL"),
        (UNRELATED_MARKDOWN, True, "ALLOW"),
        (UNRELATED_TEXT, True, "ALLOW"),
    ],
)
def test_evaluator_reaches_every_four_state_decision(
    diff_text: str, tests_passed: bool | None, expected: str
) -> None:
    assert _evaluate(diff_text, tests_passed=tests_passed).decision.value == expected


def test_same_diff_produces_identical_canonical_output() -> None:
    diff = _fixture("cross-service-dto-breaking.diff")
    first = _evaluate(diff)
    second = _evaluate(diff)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_base_revision_change_changes_hash() -> None:
    diff = _fixture("cross-service-dto-breaking.diff")
    assert _evaluate(diff, base_revisions={"revision": "a"}).base_revisions_sha256 != _evaluate(
        diff, base_revisions={"revision": "b"}
    ).base_revisions_sha256


def test_multi_file_shadow_violation_is_reported_without_blocking() -> None:
    diff = SQL_NON_TRANSACTIONAL + UNRELATED_MARKDOWN
    result = _evaluate(diff)
    assert result.decision.value == "ALLOW"
    assert result.shadow_findings


def test_unknown_boundary_preserves_owner_tests_and_evidence() -> None:
    decision = _evaluate(_fixture("dynamic-mapper.diff"))
    assert decision.decision.value == "REQUIRE_APPROVAL"
    assert decision.required_approvers == ["coupon_platform"]
    assert decision.required_tests
    assert decision.evidence


def _cli_decision(diff_path: Path) -> dict[str, object]:
    environment = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    completed = subprocess.run(
        [sys.executable, "-m", "bizguard.cli", "check", "--diff", str(diff_path)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode in (0, 1), completed.stderr
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return payload


def _mcp_decision(diff_text: str) -> dict[str, object]:
    result = asyncio.run(mcp.call_tool("get_change_decision", {"diff_text": diff_text}))
    assert isinstance(result, tuple) and len(result) == 2
    card = result[1]
    assert isinstance(card, dict)
    return card


@pytest.mark.parametrize(
    ("diff_text", "diff_path"),
    [
        (_fixture("cross-service-dto-breaking.diff"), ROOT / "bench/fixtures/phase5/cross-service-dto-breaking.diff"),
        (_fixture("dynamic-mapper.diff"), ROOT / "bench/fixtures/phase5/dynamic-mapper.diff"),
        (SQL_NON_TRANSACTIONAL, None),
        (UNRELATED_MARKDOWN, None),
    ],
)
def test_four_entry_points_agree_on_core_fields(
    tmp_path: Path, diff_text: str, diff_path: Path | None
) -> None:
    path = diff_path if diff_path is not None else tmp_path / "input.diff"
    if diff_path is None:
        path.write_text(diff_text, encoding="utf-8")

    cli = normalize_core_result(_cli_decision(path))
    mcp = normalize_core_result(_mcp_decision(diff_text))
    hook = normalize_core_result(hook_validate(diff_text))
    ci = normalize_core_result(ci_evaluate(diff_text))

    assert cli == mcp == hook == ci
