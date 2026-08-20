import pytest
from typing import Any

from bizguard.decision.v2 import DecisionInput, DecisionState, FindingV2, decide


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
