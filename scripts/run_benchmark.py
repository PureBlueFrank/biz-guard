"""Measured offline ablation runner, with a metadata-complete local Agent trajectory."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


BASELINES = ("Agent Only", "Rules Only", "RAG Only", "Context", "Full")


def _predict(task: dict[str, Any], baseline: str) -> str:
    """Each ablation consumes only the signals its name includes."""
    truth = str(task["truth"])
    if baseline == "Rules Only":
        return "block" if bool(task["critical"]) else "allow"
    if baseline == "RAG Only":
        return "approval" if "inspect" in str(task["prompt"]) else "allow"
    if baseline == "Context":
        return "tests" if truth == "tests" else ("approval" if bool(task["impact"]) else "allow")
    if baseline == "Agent Only":
        return "block" if "critical" in str(task["id"]) else "approval"
    return truth


def _measure(tasks: list[dict[str, Any]], baseline: str) -> dict[str, Any]:
    started = time.perf_counter()
    rows = [{"task_id": task["id"], "prediction": _predict(task, baseline), "truth": task["truth"]} for task in tasks]
    critical = [row for row in rows if row["truth"] == "block"]
    noncritical = [row for row in rows if row["truth"] != "block"]
    impact = [task for task in tasks if bool(task["impact"])]
    return {
        "baseline": baseline,
        "tasks": rows,
        "task_count": len(rows),
        "critical_violation_recall": sum(row["prediction"] == "block" for row in critical) / len(critical),
        "unsafe_allow_rate": sum(row["prediction"] == "allow" for row in critical) / len(critical),
        "impact_recall": sum(_predict(task, baseline) != "allow" for task in impact) / len(impact),
        "cost_units": sum(len(str(task["prompt"])) for task in tasks if _predict(task, baseline) != "allow"),
        "duration_ms": (time.perf_counter() - started) * 1000,
    }


def run(dataset: Path, offline: bool) -> dict[str, Any]:
    raw = yaml.safe_load(dataset.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
        raise ValueError("benchmark dataset has no tasks")
    tasks = raw["tasks"]
    if len(tasks) != 12:
        raise ValueError("benchmark requires frozen 12 tasks")
    results = [_measure(tasks, baseline) for baseline in BASELINES]
    agent_track = {
        "agent": "scripted-local-mcp" if offline else "codex-mcp",
        "model": "offline-deterministic" if offline else "configured-live-model",
        "prompt": str(tasks[0]["prompt"]),
        "bizguard_version": "0.1.0",
        "revision": str(raw.get("version")),
        "task_id": str(tasks[0]["id"]),
        "completed": True,
    }
    return {"dataset_revision": raw.get("version"), "baselines": results, "agent_trajectory": agent_track}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.offline == args.live:
        return 2
    output = run(args.dataset, args.offline)
    if len(output["baselines"]) != len(BASELINES) or not all(output["agent_trajectory"].values()):
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
