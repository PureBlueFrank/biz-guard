"""Deep, offline validation of the frozen Phase 2 retrieval golden set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from bizguard.knowledge.ingest import ingest_directory
from bizguard.knowledge.models import SearchRequest
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.knowledge.search import HybridSearch, LocalVectorAdapter


def evaluate(
    dataset: Path, knowledge_directory: Path | None = None, *, strict: bool = True
) -> dict[str, Any]:
    payload = yaml.safe_load(dataset.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
        raise ValueError("retrieval dataset must contain tasks")
    tasks = payload["tasks"]
    if len(tasks) != 15:
        raise ValueError("phase2 requires exactly 15 retrieval tasks")
    root = knowledge_directory or dataset.parents[3] / "knowledge" / "published"
    repo = KnowledgeRepository.memory()
    try:
        ingest_directory(root, repo)
        known = {item.id for item in repo.all()}
        records: list[dict[str, Any]] = []
        policy_hits = 0
        stale_leaks = 0
        acl_leaks = 0
        for task in tasks:
            if not isinstance(task, dict):
                raise ValueError("task must be a mapping")
            expected = list(task["expected_ids"])
            forbidden = list(task.get("forbidden_ids", []))
            forbidden_stale = list(task.get("forbidden_stale_ids", []))
            forbidden_acl = list(task.get("forbidden_acl_ids", []))
            if not set(expected + forbidden + forbidden_stale + forbidden_acl).issubset(known):
                raise ValueError(f"task {task.get('id')} references missing knowledge id")
            result = HybridSearch(repo, LocalVectorAdapter()).search(
                SearchRequest(
                    query=task["query"],
                    caller_roles=task["caller_roles"],
                    scope=task["scope"],
                    revision=task["revision"],
                )
            )
            actual = [entry.id for entry in result.entries]
            leaked = set(actual) & set(forbidden + forbidden_stale + forbidden_acl)
            stale_leaks += len(set(actual) & set(forbidden_stale))
            acl_leaks += len(set(actual) & set(forbidden_acl))
            required_policy = task.get("mandatory_policy")
            policy_ok = required_policy is None or required_policy in result.mandatory_policy_ids
            policy_hits += int(policy_ok) if required_policy else 0
            record = {
                "id": task["id"],
                "expected_ids": expected,
                "actual_ids": actual,
                "passed": actual == expected and not leaked and policy_ok,
            }
            records.append(record)
        failures = [record["id"] for record in records if not record["passed"]]
        if failures and strict:
            raise ValueError(f"retrieval golden mismatch: {', '.join(failures)}")
        required_count = sum(bool(task.get("mandatory_policy")) for task in tasks)
        return {
            "query_count": len(tasks),
            "mandatory_policy_recall": policy_hits / required_count if required_count else 1.0,
            "stale_knowledge_rate": stale_leaks / len(tasks),
            "acl_leak_count": acl_leaks,
            "records": records,
        }
    finally:
        repo.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(evaluate(args.dataset), ensure_ascii=False, sort_keys=True))
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"retrieval evaluation failed: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
