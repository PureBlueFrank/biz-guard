"""Tests for the additive lifecycle schema that leaves v1 untouched."""

from pathlib import Path

import pytest

from bizguard.domain.enums import PolicyMode
from bizguard.domain.schema_v2 import PolicyLifecycle, load_invariants_v2


ROOT = Path(__file__).parent.parent


def test_v2_policy_fixture_loads_with_lifecycle() -> None:
    schema = load_invariants_v2(ROOT / "tests" / "fixtures" / "v2" / "invariants.yaml")
    assert schema.version == 2
    assert schema.policies[0].lifecycle is PolicyLifecycle.ACTIVE
    assert schema.policies[0].mode is PolicyMode.BLOCKING


def test_v2_schema_rejects_unknown_fields(tmp_path: Path) -> None:
    invalid = tmp_path / "invariants.yaml"
    invalid.write_text(
        "version: 2\npolicies:\n  - id: p\n    lifecycle: active\n    mode: blocking\n"
        "    grandfathered_evidence: p0-fixtures-v1\n    evidence_uri: repo://x/p\n    extra: no\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="extra"):
        load_invariants_v2(invalid)
