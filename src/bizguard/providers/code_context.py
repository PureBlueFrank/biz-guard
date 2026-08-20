from bizguard.domain.models import Evidence


class CodeContextProvider:
    def collect(self) -> list[Evidence]:
        return [
            Evidence(
                id="fixture:code",
                source="AST",
                confidence=0.9,
                revision="phase3-fixture-v1",
                evidence_uri="repo://coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java#L3:C1",
            )
        ]
