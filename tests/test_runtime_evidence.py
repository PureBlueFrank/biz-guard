# mypy: disable-error-code=no-untyped-def
from pathlib import Path
from bizguard.graph.indexer import index
from bizguard.graph.runtime import import_trace


def test_trace_adds_observation():
    assert any(
        x.kind.value == "OBSERVED_CALL"
        for x in import_trace(
            index(Path("fixtures/java-microservices"), "phase3-fixture-v1"),
            Path("fixtures/traces/static-and-trace.json"),
        ).edges
    )


def test_static_only_keeps_static():
    assert import_trace(
        index(Path("fixtures/java-microservices"), "phase3-fixture-v1"),
        Path("fixtures/traces/static-only.json"),
    ).edges


def test_neither_is_not_empty_conclusion():
    assert import_trace(
        index(Path("fixtures/java-microservices"), "phase3-fixture-v1"),
        Path("fixtures/traces/neither.json"),
    ).nodes


def test_trace_only_adds_external():
    assert any(
        x.source_id == "service://external-gateway"
        for x in import_trace(
            index(Path("fixtures/java-microservices"), "phase3-fixture-v1"),
            Path("fixtures/traces/trace-only.json"),
        ).edges
    )


def test_mismatched_trace_rejected(tmp_path: Path):
    p = tmp_path / "x.json"
    p.write_text('{"revision":"other","calls":[]}')
    import pytest

    with pytest.raises(ValueError):
        import_trace(index(Path("fixtures/java-microservices"), "phase3-fixture-v1"), p)
