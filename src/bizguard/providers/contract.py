from bizguard.domain.models import Evidence


class ContractProvider:
    def collect(self) -> list[Evidence]:
        return [
            Evidence(
                id="fixture:contract",
                source="IDL",
                confidence=0.9,
                revision="phase3-fixture-v1",
                evidence_uri="repo://coupon-contract/src/main/resources/openapi.yaml#L25:C9",
            )
        ]
