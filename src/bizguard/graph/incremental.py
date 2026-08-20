"""Snapshot consistency checks for incremental callers."""

from .models import GraphSnapshot


def require_revision(snapshot: GraphSnapshot, revision: str) -> GraphSnapshot:
    """Require a snapshot to match the requested revision."""
    if snapshot.revision != revision:
        raise ValueError("INDEX_LAG: graph revision does not match change revision")
    return snapshot
