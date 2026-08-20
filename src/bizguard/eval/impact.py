"""Evaluate P3 impact paths against independently traversed graph facts."""

from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Sequence
import json
from pathlib import Path
import re

import yaml  # type: ignore[import-untyped]

from bizguard.graph.models import EdgeKind, GraphEdge, GraphSnapshot
from bizguard.impact.analyzer import analyze
from bizguard.domain.models import Evidence


def evaluate(dataset: Path, graph: Path) -> dict[str, object]:
    tasks = (yaml.safe_load(dataset.read_text(encoding="utf-8")) or {}).get("tasks", [])
    snapshot = GraphSnapshot.from_dict(json.loads(graph.read_text(encoding="utf-8")))
    ids = {node.id for node in snapshot.nodes}
    edge_ids = {edge.id for edge in snapshot.edges}
    failures: list[str] = []
    complete = 0
    unknown = 0
    required_unknown = 0
    for task in tasks:
        changed_id = _changed_id_from_diff(dataset, task, snapshot)
        result = analyze(snapshot, changed_id, snapshot.revision)
        expected_path = list(task["shortest_path"])
        if result.path != expected_path:
            failures.append(f"{task['id']}: analyzer path differs from golden")
        if not _connected(result.path, snapshot.edges):
            failures.append(f"{task['id']}: analyzer path is disconnected")
        independent = _independent_shortest_path(snapshot, changed_id)
        if independent != expected_path:
            failures.append(f"{task['id']}: golden shortest path differs from independent BFS")
        missing_nodes = set(task["expected_nodes"]) - ids
        missing_edges = set(task.get("expected_edges", [])) - edge_ids
        if missing_nodes or missing_edges:
            failures.append(f"{task['id']}: missing nodes={sorted(missing_nodes)} edges={sorted(missing_edges)}")
        if _complete_evidence(result.evidence, snapshot.revision):
            complete += 1
        else:
            failures.append(f"{task['id']}: incomplete analyzer evidence")
        if not _matches_expected_evidence(result.evidence, task.get("path_evidence", [])):
            failures.append(f"{task['id']}: analyzer evidence differs from golden evidence")
        if task.get("unknown_boundary"):
            required_unknown += 1
            if result.unknown_boundary and expected_path[-1:] == ["UNKNOWN_BOUNDARY"]:
                unknown += 1
            else:
                failures.append(f"{task['id']}: missing UNKNOWN_BOUNDARY")
    return {
        "task_count": len(tasks),
        "failures": failures,
        "path_evidence_completeness": complete / len(tasks) if tasks else 0,
        "unknown_boundary_recall": unknown / required_unknown if required_unknown else 1,
    }


def _changed_id_from_diff(dataset: Path, task: dict[str, object], snapshot: GraphSnapshot) -> str:
    diff_path = dataset.parent / str(task["diff"])
    if not diff_path.is_file():
        diff_path = Path(__file__).parents[3] / "bench/fixtures/phase3" / diff_path.name
    return changed_id_from_diff_text(snapshot, diff_path.read_text(encoding="utf-8"), str(task["id"]))


def changed_id_from_diff_text(snapshot: GraphSnapshot, text: str, label: str = "diff") -> str:
    match = re.search(r"^--- a/(.+)$", text, re.MULTILINE)
    if match is None:
        raise ValueError(f"{label}: malformed fixture diff")
    source_path = match.group(1)
    repo_path = source_path.removeprefix("fixtures/java-microservices/")
    repo, _, relative = repo_path.partition("/")
    prefix = f"repo://{repo}/{relative}#"
    proto_prefix = "proto://" if relative.endswith(".proto") else prefix
    removed = "\n".join(line[1:] for line in text.splitlines() if line.startswith("-") and not line.startswith("---"))
    tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9_]*", removed))
    candidates = [node.id for node in snapshot.nodes if node.id.startswith(proto_prefix)]
    ranked = sorted(
        candidates,
        key=lambda identifier: (
            -sum(token in identifier for token in tokens),
            len(identifier),
            identifier,
        ),
    )
    if not ranked or not any(token in ranked[0] for token in tokens):
        raise ValueError(f"{label}: diff has no indexed changed artifact")
    return ranked[0]


def _independent_shortest_path(snapshot: GraphSnapshot, start: str) -> list[str]:
    adjacency: dict[str, list[GraphEdge]] = {}
    semantic_kinds = {
        EdgeKind.EXPOSES, EdgeKind.SERIALIZES_TO, EdgeKind.MAPS_TO,
        EdgeKind.CONSUMES, EdgeKind.CALLS, EdgeKind.PUBLISHES, EdgeKind.OBSERVED_CALL,
    }
    later_kinds = {
        EdgeKind.DECLARES, EdgeKind.DEPLOYED_WITH, EdgeKind.BELONGS_TO_CAPABILITY, EdgeKind.OWNED_BY,
    }
    for edge in snapshot.edges:
        if edge.kind in semantic_kinds | later_kinds:
            adjacency.setdefault(edge.source_id, []).append(edge)
            adjacency.setdefault(edge.target_id, []).append(edge)
    queue: deque[tuple[list[str], bool]] = deque([([start], False)])
    seen = {(start, False)}
    while queue:
        path, crossed_boundary = queue.popleft()
        node = path[-1]
        if crossed_boundary and len(path) > 1 and node.startswith(("capability://", "invariant://", "owner://")):
            return path
        for edge in sorted(adjacency.get(node, []), key=lambda item: item.id):
            if edge.kind in later_kinds and not crossed_boundary:
                continue
            next_node = edge.target_id if edge.source_id == node else edge.source_id
            if next_node == start:
                continue
            next_crossed_boundary = crossed_boundary or edge.kind in semantic_kinds
            state = (next_node, next_crossed_boundary)
            if state not in seen:
                seen.add(state)
                queue.append(([ *path, next_node], next_crossed_boundary))
    start_node = next((item for item in snapshot.nodes if item.id == start), None)
    if start_node is not None and start_node.properties.get("dynamic") == "true":
        return [start, "UNKNOWN_BOUNDARY"]
    return [start]


def _connected(path: list[str], edges: list[GraphEdge]) -> bool:
    if path[-1:] == ["UNKNOWN_BOUNDARY"]:
        return True
    pairs = {(edge.source_id, edge.target_id) for edge in edges}
    return all((left, right) in pairs or (right, left) in pairs for left, right in zip(path, path[1:]))


def _complete_evidence(evidence: Sequence[Evidence], revision: str) -> bool:
    return bool(evidence) and all(
        bool(getattr(item, field))
        for item in evidence
        for field in ("id", "source", "confidence", "revision", "evidence_uri")
    ) and all(getattr(item, "revision") == revision for item in evidence)


def _matches_expected_evidence(evidence: Sequence[Evidence], expected: object) -> bool:
    if not isinstance(expected, list):
        return False
    actual = {
        (getattr(item, "source"), getattr(item, "confidence"), getattr(item, "revision"), getattr(item, "evidence_uri"))
        for item in evidence
    }
    return all(
        isinstance(item, dict)
        and (item.get("source"), item.get("confidence"), item.get("revision"), item.get("evidence_uri")) in actual
        for item in expected
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.dataset, args.graph)
    print(json.dumps(result, sort_keys=True))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
