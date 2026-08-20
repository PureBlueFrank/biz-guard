"""Conservative JPA persistence facts from parsed Java AST facts."""

from pathlib import Path
from .java_spring import JavaFact, analyze


def analyze_persistence(path: Path, repository: str, revision: str) -> list[JavaFact]:
    return [
        fact
        for fact in analyze(path, repository, revision)
        if fact.kind in {"class", "field", "method"}
    ]
