# mypy: disable-error-code=no-untyped-def
from pathlib import Path
import pytest
from bizguard.graph.indexer import index
from bizguard.graph.store import GraphStore


def test_snapshot_has_nodes():
    assert index(Path("fixtures/java-microservices"), "r").nodes


def test_snapshot_has_edges():
    assert index(Path("fixtures/java-microservices"), "r").edges


def test_snapshot_round_trip(tmp_path: Path):
    s = index(Path("fixtures/java-microservices"), "r")
    p = tmp_path / "g.json"
    GraphStore(p).save(s)
    assert GraphStore(p).load("r").revision == "r"


def test_snapshot_rejects_stale(tmp_path: Path):
    p = tmp_path / "g.json"
    GraphStore(p).save(index(Path("fixtures/java-microservices"), "r"))
    with pytest.raises(ValueError, match="INDEX_LAG"):
        GraphStore(p).load("new")


def test_schema_has_21_edges():
    from bizguard.graph.models import EdgeKind

    assert len(EdgeKind) == 21
