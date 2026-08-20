"""Regression tests for conservative legacy three-state translation."""

from dataclasses import dataclass

import pytest

from bizguard.decision import FaultCode
from bizguard.domain.compat import map_check_incomplete, map_legacy_decision
from bizguard.domain.enums import DecisionState


@dataclass
class Fault:
    code: str


@dataclass
class Card:
    decision: str
    faults: list[Fault]


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("INDEX_LAG", DecisionState.REQUIRE_APPROVAL),
        ("REVISION_MISMATCH", DecisionState.REQUIRE_APPROVAL),
        ("PERMISSION_DENIED", DecisionState.REQUIRE_APPROVAL),
        ("DYNAMIC_BOUNDARY", DecisionState.REQUIRE_APPROVAL),
        ("TEST_EVIDENCE_MISSING", DecisionState.ALLOW_WITH_TESTS),
        ("MALFORMED_INPUT", DecisionState.BLOCK),
        ("UNSUPPORTED_DIFF", DecisionState.BLOCK),
    ],
)
def test_fault_code_mapping_is_explicit(fault: str, expected: DecisionState) -> None:
    assert map_check_incomplete(fault) is expected


def test_unknown_or_missing_fault_is_conservatively_blocked() -> None:
    assert map_check_incomplete("NOT_A_FAULT") is DecisionState.BLOCK
    assert map_check_incomplete(None) is DecisionState.BLOCK


def test_check_incomplete_never_becomes_allow() -> None:
    assert map_legacy_decision(Card("CHECK_INCOMPLETE", [Fault("INDEX_LAG")])) is not DecisionState.ALLOW


@pytest.mark.parametrize("fault", list(FaultCode))
def test_every_legacy_fault_code_is_never_allowed(fault: FaultCode) -> None:
    assert map_legacy_decision(Card("CHECK_INCOMPLETE", [Fault(fault)])) is not DecisionState.ALLOW


@pytest.mark.parametrize("fault", ["garbage", "", "UNKNOWN_FAULT"])
def test_garbage_legacy_fault_is_never_allowed(fault: str) -> None:
    assert map_legacy_decision(Card("CHECK_INCOMPLETE", [Fault(fault)])) is not DecisionState.ALLOW


def test_more_severe_later_fault_wins_over_first_fault() -> None:
    card = Card("CHECK_INCOMPLETE", [Fault(FaultCode.RETRIEVAL_EMPTY), Fault(FaultCode.DIFF_PARSE)])
    assert map_legacy_decision(card) is DecisionState.BLOCK
