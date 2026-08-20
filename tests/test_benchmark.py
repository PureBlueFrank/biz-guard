from pathlib import Path

from scripts.run_benchmark import BASELINES, run


def test_all_baselines_are_measured_and_agent_metadata_complete() -> None:
    output = run(Path(__file__).parents[1] / "bench/ablations/tasks.yaml", offline=True)
    assert [row["baseline"] for row in output["baselines"]] == list(BASELINES)
    assert all(row["task_count"] == 12 for row in output["baselines"])
    assert all(output["agent_trajectory"].get(field) for field in ("agent", "model", "prompt", "bizguard_version", "revision"))
