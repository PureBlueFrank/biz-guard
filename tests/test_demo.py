"""Regression coverage for the reproducible milestone 6 demonstration."""

import json
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).parent.parent


def test_demo_script_replays_all_deterministic_outcomes() -> None:
    """The demo executes and verifies all six promised scenarios."""
    result = subprocess.run(
        ["sh", "scripts/demo.sh"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    scenario_lines = [line for line in result.stdout.splitlines() if line.startswith("场景 ")]
    assert [line.split("：", 1)[0] for line in scenario_lines] == [f"场景 {index}" for index in range(1, 7)]

    payloads = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert len(payloads) == 6

    control, blocked, allowed, incomplete, dynamic, impact = payloads
    assert control == {
        "baseline": "Naive Baseline (heuristic)",
        "bizguard_decision": "REQUIRE_APPROVAL",
        "decision": "ALLOW",
        "fixture": "cross-service-dto-breaking.diff",
    }
    assert blocked["decision"] == "BLOCK"
    assert allowed["decision"] == "ALLOW"
    assert incomplete["decision"] == "CHECK_INCOMPLETE"
    assert incomplete["faults"][0]["code"] == "input_validation"
    assert dynamic["decision"] == "REQUIRE_APPROVAL"
    assert dynamic["required_approvers"] == ["coupon_platform"]
    assert len(dynamic["evidence"]) == 1
    assert dynamic["evidence"][0].startswith("impact:DYNAMIC_BOUNDARY:")
    assert impact["unknown_boundary"] is False
    assert any("merchant-service" in node for node in impact["path"])
    assert any("coupon-core" in node for node in impact["path"])
    assert any(node.startswith("mq://") for node in impact["path"])
    assert "模拟" not in result.stdout
