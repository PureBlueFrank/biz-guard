"""Runtime observations augment, never replace, static graph evidence."""

from __future__ import annotations
import json
from pathlib import Path
from .models import EdgeKind, GraphEdge, GraphSnapshot


def import_trace(snapshot: GraphSnapshot, path: Path) -> GraphSnapshot:
    """Add runtime call observations from a trace file to a snapshot."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    revision = str(raw["revision"])
    if revision != snapshot.revision:
        raise ValueError("INDEX_LAG: trace revision mismatch")
    for item in raw.get("calls", []):
        snapshot.edges.append(
            GraphEdge(
                item["source_id"],
                item["target_id"],
                EdgeKind.OBSERVED_CALL,
                "Trace",
                float(item["confidence"]),
                revision,
                f"trace://{path.name}#{item['id']}",
                item["first_seen"],
                item["last_seen"],
            )
        )
    return snapshot
