"""Provider-facing evidence contract used by every evidence producer."""

from typing import Protocol

from bizguard.domain.models import Evidence


class EvidenceProvider(Protocol):
    """Any provider returns fully revisioned evidence, never ad-hoc dictionaries."""

    def collect(self) -> list[Evidence]:
        """Return evidence satisfying the five mandatory evidence fields."""


EvidenceContract = Evidence
