import json
import subprocess
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from bizguard.ci.check import evaluate


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "bench/fixtures/phase5/cross-service-dto-breaking.diff"


def test_ci_recomputes_expected_public_contract_decision() -> None:
    assert evaluate(FIXTURE.read_text(encoding="utf-8"))["decision"] == "REQUIRE_APPROVAL"


def test_ci_subprocess_matches_in_process() -> None:
    completed = subprocess.run([sys.executable, "-m", "bizguard.ci.check", "--diff", str(FIXTURE), "--base-revisions", "bench/fixtures/phase3-revisions.yaml", "--json"], cwd=ROOT, capture_output=True, text=True, check=True)
    revisions = yaml.safe_load((ROOT / "bench/fixtures/phase3-revisions.yaml").read_text(encoding="utf-8"))
    assert json.loads(completed.stdout) == evaluate(FIXTURE.read_text(encoding="utf-8"), revisions)


def test_ci_base_revisions_change_evidence_hash() -> None:
    diff = FIXTURE.read_text(encoding="utf-8")
    assert evaluate(diff, {"coupon-core": "base-a"})["base_revisions_sha256"] != evaluate(
        diff, {"coupon-core": "base-b"}
    )["base_revisions_sha256"]
