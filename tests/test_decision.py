"""End-to-end regression tests for the fixed BizGuard decision pipeline."""

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from bizguard.decision import Decision, FaultCode, evaluate_change


PROJECT_ROOT = Path(__file__).parent.parent
GROUND_TRUTH_PATH = PROJECT_ROOT / "tests" / "fixtures" / "ground_truth.yaml"


def load_ground_truth_cases() -> list[tuple[str, Decision]]:
    """Load every frozen decision expectation into pytest parameters."""
    ground_truth = yaml.safe_load(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    return [
        (entry["file"], Decision[entry["expected_decision"]])
        for entry in ground_truth["diffs"]
    ]


GROUND_TRUTH_CASES = load_ground_truth_cases()


@pytest.mark.parametrize(
    ("relative_path", "expected"),
    GROUND_TRUTH_CASES,
    ids=[path for path, _ in GROUND_TRUTH_CASES],
)
def test_decision_pipeline_matches_ground_truth(relative_path: str, expected: Decision) -> None:
    """Every frozen fixture keeps its expected decision."""
    source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

    assert evaluate_change(source).decision is expected


def test_ground_truth_contains_all_expected_fixtures() -> None:
    """The frozen suite has regression coverage for all 14 fixtures."""
    assert len(GROUND_TRUTH_CASES) == 14


def test_non_unified_diff_returns_a_card_instead_of_raising() -> None:
    """Malformed diff input remains a machine-readable incomplete decision."""
    card = evaluate_change("not a unified diff")

    assert card.decision is Decision.CHECK_INCOMPLETE
    assert card.faults[0].code is FaultCode.DIFF_PARSE
