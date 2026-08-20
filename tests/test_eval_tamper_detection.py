from pathlib import Path

import yaml  # type: ignore[import-untyped]

from bizguard.eval.impact import evaluate
from bizguard.graph.indexer import index
from bizguard.graph.store import GraphStore


ROOT = Path(__file__).parents[1]
DATASET = ROOT / "bench/golden/impact/phase3.yaml"


def _graph(tmp_path: Path) -> Path:
    path = tmp_path / "graph.json"
    GraphStore(path).save(index(ROOT / "fixtures/java-microservices", "phase3-fixture-v1"))
    return path


def _dataset(tmp_path: Path) -> Path:
    target = tmp_path / "phase3.yaml"
    target.write_text(DATASET.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def _rewrite_dataset(path: Path, payload: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_middle_shortest_path_mutation_fails(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    raw = yaml.safe_load(dataset.read_text(encoding="utf-8"))
    raw["tasks"][0]["shortest_path"][1] = "repo://coupon-core"
    _rewrite_dataset(dataset, raw)
    assert evaluate(dataset, _graph(tmp_path))["failures"]


def test_removing_unknown_boundary_lowers_recall(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    raw = yaml.safe_load(dataset.read_text(encoding="utf-8"))
    raw["tasks"][-1]["shortest_path"] = raw["tasks"][-1]["shortest_path"][:-1]
    _rewrite_dataset(dataset, raw)
    report = evaluate(dataset, _graph(tmp_path))
    assert report["unknown_boundary_recall"] == 0.0
    assert report["failures"]


def test_path_evidence_revision_mutation_fails(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    raw = yaml.safe_load(dataset.read_text(encoding="utf-8"))
    raw["tasks"][0]["path_evidence"][0]["revision"] = "forged-revision"
    _rewrite_dataset(dataset, raw)
    assert evaluate(dataset, _graph(tmp_path))["failures"]


def test_expected_edge_mutation_fails(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    raw = yaml.safe_load(dataset.read_text(encoding="utf-8"))
    raw["tasks"][0]["expected_edges"] = ["CALLS:not-a-real-edge"]
    _rewrite_dataset(dataset, raw)
    assert evaluate(dataset, _graph(tmp_path))["failures"]
