from bizguard.domain.models import Evidence


class ServiceCatalogProvider:
    def collect(self) -> list[Evidence]:
        return [
            Evidence(
                id="fixture:catalog",
                source="catalog",
                confidence=1,
                revision="phase3-fixture-v1",
                evidence_uri="catalog://semantic/catalog.yaml#coupon_redemption",
            )
        ]
