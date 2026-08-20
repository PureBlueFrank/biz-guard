"""Strict Golden evaluator: validates expected facts against the independently built graph."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import yaml  # type: ignore[import-untyped]
from bizguard.graph.models import GraphSnapshot


def evaluate(dataset: Path, graph: Path) -> dict[str, object]:
    tasks = (yaml.safe_load(dataset.read_text(encoding="utf-8")) or {}).get("tasks", [])
    snap = GraphSnapshot.from_dict(json.loads(graph.read_text(encoding="utf-8")))
    ids = {node.id for node in snap.nodes}
    edge_ids = {edge.id for edge in snap.edges}
    failures = []
    complete = 0
    unknown = 0
    required_unknown = 0
    for task in tasks:
        missing_nodes = set(task["expected_nodes"]) - ids
        missing_edges = set(task.get("expected_edges", [])) - edge_ids
        path = task["shortest_path"]
        if any(
            not item.get("evidence_uri") or item.get("revision") != snap.revision
            for item in task["path_evidence"]
        ):
            failures.append(task["id"] + ": incomplete path evidence")
        else:
            complete += 1
        if task.get("unknown_boundary"):
            required_unknown += 1
            if path[-1] == "UNKNOWN_BOUNDARY":
                unknown += 1
            else:
                failures.append(task["id"] + ": missing UNKNOWN_BOUNDARY")
        if missing_nodes or missing_edges:
            failures.append(
                task["id"]
                + f": missing nodes={sorted(missing_nodes)} edges={sorted(missing_edges)}"
            )
    return {
        "task_count": len(tasks),
        "failures": failures,
        "path_evidence_completeness": complete / len(tasks) if tasks else 0,
        "unknown_boundary_recall": unknown / required_unknown if required_unknown else 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True)
    a = parser.parse_args()
    result = evaluate(a.dataset, a.graph)
    print(json.dumps(result, sort_keys=True))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
