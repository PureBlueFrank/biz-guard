"""``python -m`` graph snapshot builder."""

from __future__ import annotations
import argparse
from pathlib import Path
import yaml  # type: ignore[import-untyped]
from .indexer import index
from .runtime import import_trace
from .store import GraphStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos", type=Path, required=True)
    parser.add_argument("--revision-set", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--trace", type=Path, action="append", default=[])
    args = parser.parse_args()
    raw = yaml.safe_load(args.revision_set.read_text(encoding="utf-8")) or {}
    revision = str(raw.get("revision", "phase3-fixture-v1"))
    snapshot = index(args.repos, revision)
    for trace in args.trace:
        snapshot = import_trace(snapshot, trace)
    GraphStore(args.out).save(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
