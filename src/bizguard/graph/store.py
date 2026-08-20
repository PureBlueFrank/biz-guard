"""Small durable JSON store; snapshots are always queried by exact revision."""

from __future__ import annotations
import json
from pathlib import Path
from .models import GraphSnapshot


class GraphStore:
    """Persist graph snapshots as JSON."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def save(self, snapshot: GraphSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(snapshot.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )

    def load(self, revision: str | None = None) -> GraphSnapshot:
        snapshot = GraphSnapshot.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        if revision is not None and snapshot.revision != revision:
            raise ValueError(f"INDEX_LAG: requested {revision}, indexed {snapshot.revision}")
        return snapshot
