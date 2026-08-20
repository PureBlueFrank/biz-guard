from bizguard.domain.models import Evidence


class RuntimeEvidenceProvider:
    def collect(self) -> list[Evidence]:
        return [
            Evidence(
                id="fixture:trace",
                source="Trace",
                confidence=0.8,
                revision="phase3-fixture-v1",
                evidence_uri="trace://static-and-trace.json#call-1",
            )
        ]
