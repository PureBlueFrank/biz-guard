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


@pytest.mark.parametrize("budget", [800, 1200, 2000, 4000])
def test_budget_preserves_mandatory_evidence_and_truncates_expandable(budget: int) -> None:
    compiler = ContextCompiler(ROOT / "fixtures/java-microservices")
    revisions = {"coupon-core": "fixture-coupon-core-base", "__index__": "phase3-fixture-v1"}
    pack = compiler.compile("update coupon redemption status", ["coupon-core"], revisions, token_budget=budget)

    assert pack.mandatory_policy_recall == 1.0
    assert pack.mandatory.items and pack.mandatory.evidence_ids
    assert all(item["id"] in pack.mandatory.evidence_ids for item in pack.evidence)
    expandable = ContextLayer(name="Expandable", items=[{"trace": "evidence " * budget}])
    ContextCompiler._apply_budget(budget, pack.mandatory, expandable)
    assert expandable.truncated


def test_mutating_a_frozen_context_golden_makes_verifier_fail(tmp_path: Path) -> None:
    """The verifier compares compiler output with the frozen golden, not with itself."""
    for directory in ("bench", "fixtures", "knowledge", "src", "sample"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    manifest = tmp_path / "bench/fixtures/manifest.yaml"
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["phase4"]["tasks"][0]["context_sha256"] = "0" * 64
    manifest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    assert verify(manifest, "phase4") == 1
