"""Policy lifecycle with fixture-configured promotion gates and rollback."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class PolicyMode(StrEnum):
    """Enumerate the supported policy enforcement stages."""

    DRAFT = "draft"
    SHADOW = "shadow"
    WARNING = "warning"
    BLOCKING = "blocking"


class PromotionGates(BaseModel):
    """Define the measured thresholds required for policy promotion."""

    min_samples: int = Field(ge=1)
    max_false_positive_rate: float = Field(ge=0.0, le=1.0)


class PolicyLifecycle(BaseModel):
    """Track a policy's enforcement mode and promotion evidence."""

    policy_id: str
    mode: PolicyMode = PolicyMode.DRAFT
    samples: int = 0
    false_positives: int = 0

    def promote(self, gates: PromotionGates) -> None:
        """Advance one stage only after real fixture/run statistics meet its gate."""
        if self.mode is PolicyMode.BLOCKING:
            raise ValueError("blocking policy cannot be promoted")
        if self.samples < gates.min_samples:
            raise ValueError("insufficient samples for promotion")
        rate = self.false_positives / self.samples
        if rate > gates.max_false_positive_rate:
            raise ValueError("false positive rate exceeds promotion gate")
        self.mode = PolicyMode(list(PolicyMode)[list(PolicyMode).index(self.mode) + 1])

    def rollback(self) -> None:
        if self.mode is PolicyMode.DRAFT:
            raise ValueError("draft policy cannot be rolled back")
        self.mode = PolicyMode(list(PolicyMode)[list(PolicyMode).index(self.mode) - 1])
