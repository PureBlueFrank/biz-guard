"""MQ facts are syntax-derived from Java comments/nodes, never path guessing."""

from pathlib import Path
from .java_spring import JavaFact, analyze


def analyze_messaging(path: Path, repository: str, revision: str) -> list[JavaFact]:
    """Extract messaging-relevant Java facts from a source file."""
    return [
        fact
        for fact in analyze(path, repository, revision)
        if fact.kind in {"class", "method", "field"}
    ]
