import json
from pathlib import Path

import scripts.run_benchmark as benchmark
import yaml  # type: ignore[import-untyped]
from pytest import MonkeyPatch

from bizguard.context.compiler import ContextCompiler

from scripts.run_benchmark import BASELINES, TRANSCRIPT, _predict, run


ROOT = Path(__file__).parents[1]
DATASET = ROOT / "bench/ablations/tasks.yaml"


def test_all_baselines_execute_real_fixtures_and_measure_distinct_results() -> None:
    output = run(DATASET, offline=True)

    assert [row["baseline"] for row in output["baselines"]] == list(BASELINES)
    assert all(row["task_count"] == 12 for row in output["baselines"])
    assert len({row["unsafe_allow_rate"] for row in output["baselines"]}) > 1 or len(
        {row["impact_recall"] for row in output["baselines"]}
    ) > 1
    assert output["baselines"][-1]["tasks"][0]["prediction"] == "block"


def test_run_reuses_one_context_compiler_for_all_five_baselines(monkeypatch: MonkeyPatch) -> None:
    created = 0

    def create(repositories_root: Path, *, reuse_index: bool = False) -> ContextCompiler:
        nonlocal created
        created += 1
        return ContextCompiler(repositories_root, reuse_index=reuse_index)

    monkeypatch.setattr("scripts.run_benchmark.ContextCompiler", create)
    benchmark.run(DATASET, offline=True)
    assert created == 1


def test_every_task_references_a_readable_real_diff_fixture() -> None:
    payload = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    tasks = payload["tasks"]

    assert len(tasks) == 12
    assert all((DATASET.parent / task["diff"]).resolve().is_file() for task in tasks)


def test_offline_trajectory_is_loaded_from_recorded_mcp_transcript() -> None:
    output = run(DATASET, offline=True)
    transcript = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))

    assert output["agent_trajectory"] == transcript
    assert transcript["tool_calls"][0]["tool"] == "bizguard.validate_patch"
    assert all(transcript.get(field) for field in ("agent", "model", "prompt", "bizguard_version", "revision", "task_id", "decision", "duration_ms"))
    assert "diff_text" in transcript["tool_calls"][0]["input"]


def test_full_matches_all_twelve_golden_task_outcomes() -> None:
    payload = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    assert [_predict(task, "Full", DATASET) for task in payload["tasks"]] == [task["truth"] for task in payload["tasks"]]


def test_prediction_changes_when_its_real_diff_content_changes(tmp_path: Path) -> None:
    payload = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    task = next(item for item in payload["tasks"] if item["id"] == "critical-ledger-1")
    original = dict(task)
    fixture = tmp_path / "changed.diff"
    fixture.write_text(
        (ROOT / "sample/diffs/diff_normal_1.diff").read_text(encoding="utf-8"), encoding="utf-8"
    )
    task["diff"] = "changed.diff"
    altered = tmp_path / "tasks.yaml"
    altered.write_text(yaml.safe_dump({"version": "temporary", "tasks": [task] * 12}), encoding="utf-8")

    assert _predict(original, "Rules Only", DATASET) == "block"
    assert _predict(task, "Rules Only", altered) == "allow"
