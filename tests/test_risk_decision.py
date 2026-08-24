import pytest
from typing import Any

from bizguard.decision.v2 import DecisionInput, DecisionState, FindingV2, decide
from bizguard.policy.lifecycle import PolicyMode
from bizguard.risk.engine import score


def _finding(**values: Any) -> FindingV2:
    return FindingV2(id="f", effect="effect", remediation="fix", confidence=1.0, **values)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (DecisionInput(findings=[_finding(severity="critical", violated=True)], required_tests=["t"], tests_passed=False), DecisionState.BLOCK),
        (DecisionInput(findings=[_finding(critical_unknown=True)]), DecisionState.REQUIRE_APPROVAL),
        (DecisionInput(version_known=False), DecisionState.REQUIRE_APPROVAL),
        (DecisionInput(required_tests=["t"], tests_passed=False), DecisionState.ALLOW_WITH_TESTS),
        (DecisionInput(findings=[_finding(public_contract=True)]), DecisionState.REQUIRE_APPROVAL),
        (DecisionInput(owners=["a", "b"]), DecisionState.REQUIRE_APPROVAL),
        (DecisionInput(risk_score=0.9), DecisionState.REQUIRE_APPROVAL),
        (DecisionInput(), DecisionState.ALLOW),
        (DecisionInput(required_tests=["t"], tests_passed=True), DecisionState.ALLOW),
        (DecisionInput(findings=[_finding(severity="high", violated=True)]), DecisionState.ALLOW),
        (DecisionInput(findings=[_finding(required_approver="a")], owners=["a"]), DecisionState.ALLOW),
        (DecisionInput(findings=[_finding(critical_unknown=True, severity="critical", violated=True)]), DecisionState.BLOCK),
    ],
)
def test_ordered_four_state_decision(data: DecisionInput, expected: DecisionState) -> None:
    assert decide(data).decision is expected


def test_shadow_violation_is_observable_but_does_not_gate() -> None:
    result = decide(
        DecisionInput(
            findings=[
                _finding(
                    severity="critical",
                    violated=True,
                    policy_mode=PolicyMode.SHADOW,
                )
            ]
        )
    )
    assert result.decision is DecisionState.ALLOW
    assert result.shadow_findings == ["f"]


def test_passed_shadow_check_is_not_counted_as_a_hit() -> None:
    result = decide(
        DecisionInput(
            findings=[
                _finding(
                    violated=False,
                    policy_mode=PolicyMode.SHADOW,
                )
            ]
        )
    )
    assert result.decision is DecisionState.ALLOW
    assert result.shadow_findings == []


def test_warning_violation_requires_approval_instead_of_blocking() -> None:
    result = decide(
        DecisionInput(
            findings=[
                _finding(
                    severity="critical",
                    violated=True,
                    policy_mode=PolicyMode.WARNING,
                )
            ]
        )
    )
    assert result.decision is DecisionState.REQUIRE_APPROVAL


def test_passed_findings_do_not_dilute_or_inflate_material_risk() -> None:
    risky = FindingV2(
        id="risky",
        severity="high",
        effect="effect",
        remediation="fix",
        confidence=0.9,
        violated=True,
    )
    passed = [_finding(severity="medium") for _ in range(4)]
    assert score([risky]) == score([risky, *passed]) == pytest.approx(0.585)
