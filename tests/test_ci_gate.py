"""CI gate exit-code contract and subprocess verification."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from bizguard.ci.check import gate_exit_code


ROOT = Path(__file__).parents[1]
REVISIONS = ROOT / "bench" / "fixtures" / "phase3-revisions.yaml"


@pytest.mark.parametrize(
    ("decision", "tests_complete", "approved", "expected"),
    [
        ("ALLOW", False, False, 0),
        ("ALLOW_WITH_TESTS", True, False, 0),
        ("ALLOW_WITH_TESTS", False, False, 1),
        ("REQUIRE_APPROVAL", False, True, 0),
        ("REQUIRE_APPROVAL", False, False, 1),
        ("BLOCK", False, False, 1),
        ("UNKNOWN", False, False, 2),
    ],
)
def test_gate_exit_code_contract(
    decision: str, tests_complete: bool, approved: bool, expected: int
) -> None:
    assert gate_exit_code(decision, tests_complete=tests_complete, approved=approved) == expected


def _run_gate(diff_path: Path, revisions: Path | None = None) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, "-m", "bizguard.ci.check", "--diff", str(diff_path)]
    args += ["--base-revisions", str(revisions or REVISIONS)]
    args += ["--json"]
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)


def test_block_fixture_returns_exit_one() -> None:
    diff = ROOT / "sample" / "diffs" / "diff_violation_1.diff"
    completed = _run_gate(diff)
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["decision"] == "BLOCK"


def test_unapproved_approval_fixture_returns_non_zero() -> None:
    diff = ROOT / "bench" / "fixtures" / "phase5" / "dynamic-mapper.diff"
    completed = _run_gate(diff)
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["decision"] == "REQUIRE_APPROVAL"


def test_multi_file_violation_in_second_file_fails(tmp_path: Path) -> None:
    first = """\
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,1 +1,1 @@
-# title
+# title v2
"""
    second = """\
diff --git a/coupon-core/src/main/resources/db/V2__ledger.sql b/coupon-core/src/main/resources/db/V2__ledger.sql
--- a/coupon-core/src/main/resources/db/V2__ledger.sql
+++ b/coupon-core/src/main/resources/db/V2__ledger.sql
@@ -1,1 +1,1 @@
-UPDATE ledger SET status='SUCCESS';
+UPDATE ledger SET status='FAILED';
"""
    diff_path = tmp_path / "multi.diff"
    diff_path.write_text(first + second, encoding="utf-8")
    completed = _run_gate(diff_path)
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["decision"] == "BLOCK"


def test_missing_base_revisions_returns_exit_two(tmp_path: Path) -> None:
    diff_path = tmp_path / "input.diff"
    diff_path.write_text("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n-a\n+b\n", encoding="utf-8")
    completed = _run_gate(diff_path, revisions=tmp_path / "missing.yaml")
    assert completed.returncode == 2


def test_unparsable_diff_never_passes(tmp_path: Path) -> None:
    diff_path = tmp_path / "bad.diff"
    diff_path.write_text("this is not a unified diff\n", encoding="utf-8")
    completed = _run_gate(diff_path)
    assert completed.returncode != 0
