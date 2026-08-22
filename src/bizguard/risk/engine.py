"""Risk scoring used only after four-state hard gates."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bizguard.decision.v2 import FindingV2


_WEIGHTS = {"low": 0.15, "medium": 0.35, "high": 0.65, "critical": 1.0}


def score(findings: list[FindingV2]) -> float:
    """Return a bounded evidence-weighted score, never a decision itself."""
    if not findings:
        return 0.0
    return min(1.0, sum(_WEIGHTS.get(item.severity, 0.35) * item.confidence for item in findings) / len(findings))
