"""Benchmark metric computation and holdout dataset contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from scripts.run_benchmark import _compute_metrics, _load_tasks


ROOT = Path(__file__).parents[1]


def _rows() -> list[dict[str, Any]]:
    return [
        {"task_id": "1", "prediction": "block", "truth": "block", "impact": True},
        {"task_id": "2", "prediction": "block", "truth": "block", "impact": False},
        {"task_id": "3", "prediction": "allow", "truth": "allow", "impact": False},
        {"task_id": "4", "prediction": "tests", "truth": "tests", "impact": True},
        {"task_id": "5", "prediction": "approval", "truth": "approval", "impact": False},
    ]


def test_perfect_predictions_yield_unit_recall_and_zero_over_block() -> None:
    metrics = _compute_metrics(_rows())
    assert metrics["critical_violation_recall"] == 1.0
    assert metrics["unsafe_allow_rate"] == 0.0
    assert metrics["false_block_rate"] == 0.0
    assert metrics["approval_precision"] == 1.0
    assert metrics["approval_recall"] == 1.0
    assert metrics["required_test_recall"] == 1.0
    assert metrics["impact_recall"] == 1.0
    assert metrics["macro_f1"] == 1.0


def test_always_block_exposes_false_block_rate() -> None:
    rows = [
        {"task_id": "1", "prediction": "block", "truth": "allow", "impact": False},
        {"task_id": "2", "prediction": "block", "truth": "tests", "impact": False},
        {"task_id": "3", "prediction": "block", "truth": "block", "impact": False},
    ]
    metrics = _compute_metrics(rows)
    assert metrics["false_block_rate"] == 1.0
    assert metrics["critical_violation_recall"] == 1.0
    assert metrics["unsafe_allow_rate"] == 0.0


def test_missed_blocks_lower_critical_recall_and_raise_unsafe_allow() -> None:
    rows = [
        {"task_id": "1", "prediction": "allow", "truth": "block", "impact": False},
        {"task_id": "2", "prediction": "block", "truth": "block", "impact": False},
        {"task_id": "3", "prediction": "allow", "truth": "allow", "impact": False},
    ]
    metrics = _compute_metrics(rows)
    assert metrics["critical_violation_recall"] == 0.5
    assert metrics["unsafe_allow_rate"] == 0.5
    assert metrics["false_block_rate"] == 0.0


def test_holdout_has_twenty_plus_tasks_with_separate_truth() -> None:
    tasks = yaml.safe_load((ROOT / "bench/holdout/tasks.yaml").read_text(encoding="utf-8"))["tasks"]
    truth = yaml.safe_load((ROOT / "bench/holdout/truth.yaml").read_text(encoding="utf-8"))["tasks"]
    assert len(tasks) >= 20
    assert len(truth) == len(tasks)
    assert all("truth" not in task for task in tasks)


def test_load_tasks_joins_separate_truth() -> None:
    tasks = _load_tasks(ROOT / "bench/holdout/tasks.yaml")
    assert len(tasks) >= 20
    assert all("truth" in task for task in tasks)
    truth_map = {
        item["id"]: item["truth"]
        for item in yaml.safe_load((ROOT / "bench/holdout/truth.yaml").read_text(encoding="utf-8"))["tasks"]
    }
    assert {task["id"]: task["truth"] for task in tasks} == truth_map


def test_holdout_covers_all_four_states() -> None:
    truth = [
        item["truth"]
        for item in yaml.safe_load((ROOT / "bench/holdout/truth.yaml").read_text(encoding="utf-8"))["tasks"]
    ]
    assert {"block", "tests", "approval", "allow"} <= set(truth)
