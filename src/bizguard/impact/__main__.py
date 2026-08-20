"""Module entrypoint for diff-driven impact analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from bizguard.eval.impact import changed_id_from_diff_text
from bizguard.graph.indexer import index
from bizguard.impact.service import ImpactService


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    analyze_parser = commands.add_parser("analyze")
    analyze_parser.add_argument("--diff", type=Path, required=True)
    analyze_parser.add_argument("--repos", type=Path, required=True)
    analyze_parser.add_argument("--revision-set", type=Path, required=True)
    analyze_parser.add_argument("--format", choices=["json"], default="json")
    arguments = parser.parse_args()
    if not arguments.diff.is_file() or not arguments.repos.is_dir() or not arguments.revision_set.is_file():
        return 2
    raw = yaml.safe_load(arguments.revision_set.read_text(encoding="utf-8")) or {}
    revision = str(raw.get("revision", "phase3-fixture-v1"))
    diff_text = arguments.diff.read_text(encoding="utf-8")
    changed_symbol = changed_id_from_diff_text(index(arguments.repos, revision), diff_text)
    report = ImpactService(arguments.repos).analyze(
        changed_symbol, revision, capability=None, diff_text=diff_text
    )
    print(json.dumps(report.model_dump(mode="json"), sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
