"""The frozen Phase 1 benchmark must stay complete and loadable."""

import json
import shutil
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]

from bizguard.bench.verify import verify


ROOT = Path(__file__).parent.parent
MANIFEST = ROOT / "bench" / "fixtures" / "manifest.yaml"


def test_phase1_manifest_has_twelve_unique_tasks() -> None:
    payload = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    task_ids = [task["id"] for task in payload["tasks"]]
    assert len(task_ids) == 12
    assert len(task_ids) == len(set(task_ids))


def test_phase1_manifest_and_all_goldens_load() -> None:
    assert verify(MANIFEST, "phase1") == 0


def _copy_phase1_suite(tmp_path: Path) -> Path:
    for directory in ("bench", "sample", "knowledge", "src", "fixtures"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    return tmp_path / "bench" / "fixtures" / "manifest.yaml"


def _load_manifest(path: Path) -> dict[str, object]:
    return cast(dict[str, object], yaml.safe_load(path.read_text(encoding="utf-8")))


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_verifier_rejects_broken_diff_and_duplicate_input(tmp_path: Path) -> None:
    manifest = _copy_phase1_suite(tmp_path)
    payload = _load_manifest(manifest)
    tasks = payload["tasks"]
    assert isinstance(tasks, list)
    tasks[0]["input_diff"] = "bench/fixtures/phase1/missing.diff"
    _write_manifest(manifest, payload)
    assert verify(manifest, "phase1") == 1
    tasks[0]["input_diff"] = tasks[1]["input_diff"]
    _write_manifest(manifest, payload)
    assert verify(manifest, "phase1") == 1


def test_verifier_rejects_self_loop_and_outcome_contradiction(tmp_path: Path) -> None:
    manifest = _copy_phase1_suite(tmp_path)
    impact = tmp_path / "bench" / "golden" / "impact" / "ledger-state-01.json"
    impact_payload = json.loads(impact.read_text(encoding="utf-8"))
    impact_payload["node_ids"][1] = impact_payload["node_ids"][0]
    impact.write_text(json.dumps(impact_payload), encoding="utf-8")
    assert verify(manifest, "phase1") == 1
    impact_payload["node_ids"][1] = (
        "repo://coupon-core/src/main/java/com/bizguard/coupon/application/"
        "RedeemService.java#RedeemService.redeem(CouponRequest)"
    )
    impact.write_text(json.dumps(impact_payload), encoding="utf-8")
    decision = tmp_path / "bench" / "golden" / "decision" / "idempotency-order-01.json"
    decision_payload = json.loads(decision.read_text(encoding="utf-8"))
    decision_payload["outcome"] = "ALLOW"
    decision.write_text(json.dumps(decision_payload), encoding="utf-8")
    assert verify(manifest, "phase1") == 1


def test_phase5_verifier_consumes_and_rejects_mutated_golden(tmp_path: Path) -> None:
    manifest = _copy_phase1_suite(tmp_path)
    decisions = tmp_path / "bench" / "fixtures" / "phase5" / "decision-tasks.yaml"
    payload = yaml.safe_load(decisions.read_text(encoding="utf-8"))
    payload["tasks"][0]["expected"] = "ALLOW"
    decisions.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    assert verify(manifest, "phase5") == 1
