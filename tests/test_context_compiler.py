"""Acceptance coverage for Context Pack compilation and frozen P4 goldens."""

from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import cast

import pytest
import yaml  # type: ignore[import-untyped]

from bizguard.bench.verify import verify
from bizguard.context.compiler import ContextCompiler, ContextLayer


ROOT = Path(__file__).parent.parent
MANIFEST = ROOT / "bench/fixtures/manifest.yaml"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def _tasks() -> list[dict[str, object]]:
    payload = cast(dict[str, object], yaml.safe_load(MANIFEST.read_text(encoding="utf-8")))
    phase4 = cast(dict[str, object], payload["phase4"])
    return cast(list[dict[str, object]], phase4["tasks"])


@pytest.mark.parametrize("task", _tasks(), ids=lambda item: str(item["id"]))
def test_compiler_matches_each_frozen_context_pack(task: dict[str, object]) -> None:
    compiler = ContextCompiler(ROOT / "fixtures/java-microservices")
    repositories = cast(list[str], task["repos"])
    revisions = cast(dict[str, str], task["base_revisions"])
    pack = compiler.compile(str(task["task"]), repositories, revisions)

    assert _digest(pack.model_dump(mode="json")) == task["context_sha256"]
    assert _digest(pack.impact.model_dump(mode="json")) == task["impact_sha256"]
    assert _digest(pack.required_tests) == task["required_tests_sha256"]


@pytest.mark.parametrize(
    ("budget", "expandable_truncated"),
    [(800, True), (1200, True), (2000, True), (4000, False)],
)
def test_budget_preserves_mandatory_evidence_and_truncates_expandable(
    budget: int,
    expandable_truncated: bool,
) -> None:
    compiler = ContextCompiler(ROOT / "fixtures/java-microservices")
    revisions = {"coupon-core": "fixture-coupon-core-base", "__index__": "phase3-fixture-v1"}
    pack = compiler.compile("update coupon redemption status", ["coupon-core"], revisions, token_budget=budget)

    assert pack.mandatory_policy_recall == 1.0
    assert pack.mandatory.items and pack.mandatory.evidence_ids
    assert pack.mandatory.evidence_ids
    assert pack.expandable.truncated is expandable_truncated
    assert pack.token_count <= budget


def test_budget_rejects_mandatory_context_that_cannot_fit() -> None:
    mandatory = ContextLayer(
        name="Mandatory",
        items=[{"policy_id": "required", "statement": "required rule " * 400}],
        evidence_ids=["evidence:required"],
    )
    structural = ContextLayer(name="Structural")
    rationale = ContextLayer(name="Rationale")
    expandable = ContextLayer(name="Expandable")

    with pytest.raises(ValueError, match="mandatory context exceeds token_budget"):
        ContextCompiler._apply_budget(800, mandatory, structural, rationale, expandable, [])

    assert mandatory.items
    assert mandatory.evidence_ids == ["evidence:required"]


def test_content_digest_changes_context_id_and_marks_bad_digest_stale(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    shutil.copytree(ROOT / "fixtures/java-microservices", fixtures)
    revisions = {"coupon-core": "fixture-coupon-core-base", "__index__": "sha256:" + "0" * 64}
    compiler = ContextCompiler(fixtures)
    before = compiler.compile("update coupon redemption status", ["coupon-core"], revisions)
    source = fixtures / "coupon-core/src/main/java/com/bizguard/coupon/api/CouponResponse.java"
    source.write_text(source.read_text(encoding="utf-8") + "\n// changed content\n", encoding="utf-8")
    after = compiler.compile("update coupon redemption status", ["coupon-core"], revisions)

    assert before.stale and after.stale
    assert before.graph_content_digest != after.graph_content_digest
    assert before.change_context_id != after.change_context_id


def test_mandatory_policy_recall_is_measured_after_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    def lose_mandatory(_: int, mandatory: ContextLayer, *optional: object) -> None:
        mandatory.items = []

    monkeypatch.setattr(ContextCompiler, "_apply_budget", staticmethod(lose_mandatory))
    pack = ContextCompiler(ROOT / "fixtures/java-microservices").compile(
        "update coupon redemption status", ["coupon-core"], {"coupon-core": "fixture-coupon-core-base"}
    )
    assert pack.mandatory_policy_recall == 0.0


def test_unrelated_task_is_explicitly_reported_unknown() -> None:
    compiler = ContextCompiler(ROOT / "fixtures/java-microservices")
    pack = compiler.compile("rotate zebrafish telescope", ["coupon-core"], {"coupon-core": "fixture-coupon-core-base"})
    assert "NO_MATCHING_SYMBOL" in pack.unknowns


def test_private_method_request_keeps_documented_lexical_candidate_limit() -> None:
    pack = ContextCompiler(ROOT / "fixtures/java-microservices").compile(
        "rename private redeem helper", ["coupon-core"], {"coupon-core": "fixture-coupon-core-base"}
    )
    assert pack.candidates == ["mq://coupon-core/coupon.redeemed#status"]


def test_mutating_a_frozen_context_golden_makes_verifier_fail(tmp_path: Path) -> None:
    """The verifier compares compiler output with the frozen golden, not with itself."""
    for directory in ("bench", "fixtures", "knowledge", "src", "sample"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    manifest = tmp_path / "bench/fixtures/manifest.yaml"
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["phase4"]["tasks"][0]["context_sha256"] = "0" * 64
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    assert verify(manifest, "phase4") == 1
