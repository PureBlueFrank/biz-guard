import json
import subprocess
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from bizguard.ci.check import evaluate


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "bench/fixtures/phase5/cross-service-dto-breaking.diff"
DYNAMIC_FIXTURE = ROOT / "bench/fixtures/phase5/dynamic-mapper.diff"
DYNAMIC_SYMBOL = (
    "repo://coupon-core/src/main/java/com/bizguard/coupon/persistence/"
    "DynamicCouponMapper.java#DynamicCouponMapper.map()"
)
DYNAMIC_EVIDENCE = f"impact:DYNAMIC_BOUNDARY:{DYNAMIC_SYMBOL}"


def test_ci_recomputes_expected_public_contract_decision() -> None:
    assert evaluate(FIXTURE.read_text(encoding="utf-8"))["decision"] == "ALLOW_WITH_TESTS"


def test_ci_subprocess_matches_in_process() -> None:
    completed = subprocess.run([sys.executable, "-m", "bizguard.ci.check", "--diff", str(FIXTURE), "--base-revisions", "bench/fixtures/phase3-revisions.yaml", "--json"], cwd=ROOT, capture_output=True, text=True, check=False)
    revisions = yaml.safe_load((ROOT / "bench/fixtures/phase3-revisions.yaml").read_text(encoding="utf-8"))
    assert json.loads(completed.stdout) == evaluate(FIXTURE.read_text(encoding="utf-8"), revisions)


def test_ci_base_revisions_change_evidence_hash() -> None:
    diff = FIXTURE.read_text(encoding="utf-8")
    assert evaluate(diff, {"coupon-core": "base-a"})["base_revisions_sha256"] != evaluate(
        diff, {"coupon-core": "base-b"}
    )["base_revisions_sha256"]


def test_ci_required_tests_match_impact_for_public_dto_change() -> None:
    ci_tests = evaluate(FIXTURE.read_text(encoding="utf-8"))["required_tests"]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "bizguard.impact",
            "analyze",
            "--diff",
            str(FIXTURE),
            "--repos",
            "fixtures/java-microservices",
            "--revision-set",
            "bench/fixtures/phase3-revisions.yaml",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert ci_tests == json.loads(completed.stdout)["required_tests"]


def test_ci_routes_dynamic_mapper_unknown_boundary_to_approval() -> None:
    result = evaluate(
        DYNAMIC_FIXTURE.read_text(encoding="utf-8"),
        {"revision": "phase3-fixture-v1"},
    )

    assert result["decision"] == "REQUIRE_APPROVAL"
    evidence = result["evidence"]
    assert isinstance(evidence, list)
    assert evidence == [DYNAMIC_EVIDENCE]
    assert result["required_approvers"] == ["coupon_platform"]


def test_verify_install_uses_existing_default_fixture() -> None:
    completed = subprocess.run(
        ["./scripts/verify_install.sh", "--offline"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert '"mcp_ok": true' in completed.stdout
    assert '"decision": "REQUIRE_APPROVAL"' in completed.stdout
    assert DYNAMIC_EVIDENCE in completed.stdout


def test_verify_install_replays_dynamic_mapper_fixture_from_any_cwd(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            str(ROOT / "scripts/verify_install.sh"),
            "--offline",
            "--fixture",
            "bench/fixtures/phase5/dynamic-mapper.diff",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert '"tool_count": 8' in completed.stdout
    assert '"unknown_reason": "DYNAMIC_BOUNDARY"' in completed.stdout
    assert DYNAMIC_EVIDENCE in completed.stdout
