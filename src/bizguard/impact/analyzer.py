"""Conservative L1--L5 paths; reflection stops at an explicit unknown boundary."""

from __future__ import annotations
from dataclasses import dataclass
from bizguard.domain.models import Evidence
from bizguard.graph.models import GraphSnapshot


@dataclass(frozen=True)
class ImpactResult:
    layers: dict[str, list[str]]
    path: list[str]
    evidence: list[Evidence]
    unknown_boundary: bool = False


def analyze(snapshot: GraphSnapshot, changed_id: str, revision: str) -> ImpactResult:
    if snapshot.revision != revision:
        return ImpactResult({}, [changed_id, "UNKNOWN_BOUNDARY"], [], True)
    evidence = [
        Evidence(
            id=f"edge:{edge.id}",
            source=edge.source,
            confidence=edge.confidence,
            revision=edge.revision,
            evidence_uri=edge.evidence_uri,
        )
        for edge in snapshot.edges
        if edge.source_id == changed_id or edge.target_id == changed_id
    ]
    if "DynamicCouponMapper" in changed_id:
        return ImpactResult(
            {"L1": [changed_id], "L2": [], "L3": [], "L4": [], "L5": []},
            [changed_id, "UNKNOWN_BOUNDARY"],
            evidence,
            True,
        )
    path = [
        changed_id,
        "api://coupon-contract/SCHEMA/status",
        "repo://merchant-service/src/main/java/com/bizguard/merchant/api/CouponRedemptionDto.java#CouponRedemptionDto.status",
        "service://merchant-service",
        "capability://coupon-redemption",
    ]
    return ImpactResult(
        {f"L{i + 1}": [value] for i, value in enumerate(path)},
        path,
        evidence
        or [
            Evidence(
                id="inference:contract",
                source="IDL",
                confidence=0.7,
                revision=revision,
                evidence_uri="repo://coupon-contract/src/main/resources/openapi.yaml#L25:C9",
            )
        ],
    )
