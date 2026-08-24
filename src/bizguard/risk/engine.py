"""Risk scoring used only after four-state hard gates."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bizguard.decision.v2 import FindingV2


_WEIGHTS = {"low": 0.15, "medium": 0.35, "high": 0.65, "critical": 1.0}


def score(findings: list[FindingV2]) -> float:
    """Return the strongest material risk signal without dilution by passed checks."""
    material = [
        item for item in findings if item.violated or item.critical_unknown or item.public_contract
    ]
    if not material:
        return 0.0
    return min(
        1.0,
        max(_WEIGHTS.get(item.severity, 0.35) * item.confidence for item in material),
    )
