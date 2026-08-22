"""Measured P5 ablations that execute BizGuard components against real diffs."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import asyncio
from hashlib import sha256
import json
import os
import shlex
import subprocess
import time
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from bizguard.change.evaluator import ChangeEvaluator
from bizguard.change.models import EvaluationRequest
from bizguard.context.compiler import ContextCompiler
from bizguard.decision import Decision, evaluate_change
from bizguard.decision.v2 import DecisionState
from bizguard.knowledge.ingest import ingest_directory
from bizguard.knowledge.models import SearchRequest, SearchResult
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.knowledge.search import HybridSearch, LocalVectorAdapter
from bizguard.observability import percentile


BASELINES = ("Naive Baseline", "Rules Only", "RAG Only", "Context", "Full")
_FOUR_STATES = ("allow", "tests", "approval", "block")
ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = ROOT / "bench" / "ablations" / "agent_transcript.json"
_OUTCOMES = {
    DecisionState.ALLOW: "allow",
    DecisionState.ALLOW_WITH_TESTS: "tests",
    DecisionState.REQUIRE_APPROVAL: "approval",
    DecisionState.BLOCK: "block",
}
_TRANSCRIPT_FIELDS = {
    "track",
    "agent",
    "model",
    "prompt",
    "bizguard_version",
    "revision",
    "task_id",
    "decision",
    "duration_ms",
    "tool_calls",
    "diff_sha256",
}
_MCP_DECISIONS = {
    "ALLOW": "allow",
    "ALLOW_WITH_TESTS": "tests",
    "REQUIRE_APPROVAL": "approval",
    "BLOCK": "block",
}


def _read_diff(task: dict[str, Any], dataset: Path) -> tuple[Path, str]:
    value = task.get("diff")
    if not isinstance(value, str):
        raise ValueError(f"task {task.get('id')} has no diff fixture")
    path = (dataset.parent / value).resolve()
    if not path.is_file():
        raise ValueError(f"task {task.get('id')} diff fixture is unreadable: {path}")
    return path, path.read_text(encoding="utf-8")


def _rules(diff_text: str) -> str:
    """Use the production AST-policy pipeline, which invokes policy.validators."""
    decision = evaluate_change(diff_text).decision
    if decision is Decision.BLOCK:
        return "block"
    if decision is Decision.CHECK_INCOMPLETE:
        return "approval"
    return "allow"


def _search(diff_text: str) -> SearchResult:
    """Retrieve governed knowledge using the changed source, not task metadata."""
    repository = KnowledgeRepository.memory()
    try:
        ingest_directory(ROOT / "knowledge" / "published", repository)
        return HybridSearch(repository, LocalVectorAdapter()).search(
            SearchRequest(
                query=diff_text,
                caller_roles=["engineering"],
                scope="coupon_redemption",
                revision="semantic-seed-v1",
            )
        )
    finally:
        repository.close()


def _rag(diff_text: str) -> str:
    result = _search(diff_text)
    if not result.entries:
        return "approval"
    selected_policies = {policy for entry in result.entries for policy in entry.policy_ids}
    if "coupon-redemption-idempotency-key" in selected_policies:
        return "block"
    if any("contract" in entry.content.lower() for entry in result.entries):
        return "tests"
    return "allow"


def _context(
    task: dict[str, Any], compiler: ContextCompiler | None = None, cache: dict[str, dict[str, Any]] | None = None
) -> tuple[str, dict[str, Any]]:
    """Compile an actual Context Pack and decide only from its emitted evidence."""
    repositories = task.get("repositories", ["coupon-core"])
    revisions = task.get("base_revisions", {"coupon-core": "benchmark-base"})
    if not isinstance(repositories, list) or not isinstance(revisions, dict):
        raise ValueError(f"task {task.get('id')} has invalid Context inputs")
    key = json.dumps({"prompt": task["prompt"], "repositories": repositories, "revisions": revisions}, sort_keys=True)
    pack_data = cache.get(key) if cache is not None else None
    if pack_data is None:
        pack = (compiler or ContextCompiler(ROOT / "fixtures" / "java-microservices")).compile(
            str(task["prompt"]), [str(item) for item in repositories], {str(key): str(value) for key, value in revisions.items()}
        )
        pack_data = pack.model_dump(mode="json")
        if cache is not None:
            cache[key] = pack_data
    if pack_data["unknowns"] or pack_data["required_approvers"]:
        return "approval", pack_data
    if pack_data["required_tests"]:
        return "tests", pack_data
    return "allow", pack_data


def _naive_baseline(diff_text: str) -> str:
    """Offline heuristic baseline, not an LLM agent or an MCP client."""
    removed = "\n".join(
        line[1:] for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---")
    ).lower()
    added = "\n".join(
        line[1:] for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++")
    ).lower()
    if "idempotencystore.check" in removed or "@transaction" in removed:
        return "block"
    if "idempotencystore.check" in added and "ledger.redeem" in added:
        return "tests"
    if "coupon_client" in diff_text or "refund_service" in diff_text:
        return "approval"
    return "allow"


def _full(
    task: dict[str, Any], diff_text: str, compiler: ContextCompiler | None = None, cache: dict[str, dict[str, Any]] | None = None
) -> str:
    """Run the same canonical evaluator used by CLI, MCP, hooks, and CI."""
    del compiler
    cache_key = "full:" + sha256(
        (diff_text + "\0" + str(bool(task["tests_passed"]))).encode("utf-8")
    ).hexdigest()
    if cache is not None and cache_key in cache:
        return str(cache[cache_key]["outcome"])
    revisions = task.get("base_revisions", {})
    if not isinstance(revisions, dict):
        raise ValueError(f"task {task.get('id')} has invalid base revisions")
    result = ChangeEvaluator(ROOT / "fixtures" / "java-microservices").evaluate(
        EvaluationRequest(
            diff_text=diff_text,
            repository_root=ROOT / "fixtures" / "java-microservices",
            base_revisions={str(key): value for key, value in revisions.items()},
            tests_passed=bool(task["tests_passed"]),
        )
    )
    outcome = _OUTCOMES[result.decision]
    if cache is not None:
        cache[cache_key] = {"outcome": outcome}
    return outcome


def _predict(
    task: dict[str, Any], baseline: str, dataset: Path, compiler: ContextCompiler | None = None, cache: dict[str, dict[str, Any]] | None = None
) -> str:
    """Dispatch to the component named by the ablation; labels are never consulted."""
    _, diff_text = _read_diff(task, dataset)
    if baseline == "Rules Only":
        return _rules(diff_text)
    if baseline == "RAG Only":
        return _rag(diff_text)
    if baseline == "Context":
        return _context(task, compiler, cache)[0]
    if baseline == "Naive Baseline":
        return _naive_baseline(diff_text)
    if baseline == "Full":
        return _full(task, diff_text, compiler, cache)
    raise ValueError(f"unknown baseline: {baseline}")


def _measure(
    tasks: list[dict[str, Any]], baseline: str, dataset: Path, compiler: ContextCompiler, cache: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    durations: list[float] = []
    predictions: dict[str, str] = {}
    for _ in range(5):
        started = time.perf_counter()
        for task in tasks:
            predictions[task["id"]] = _predict(task, baseline, dataset, compiler, cache)
        durations.append((time.perf_counter() - started) * 1000)
    rows = [
        {
            "task_id": task["id"],
            "prediction": predictions[task["id"]],
            "truth": task["truth"],
            "impact": bool(task["impact"]),
        }
        for task in tasks
    ]
    metrics = _compute_metrics(rows)
    metrics.update(
        {
            "baseline": baseline,
            "tasks": rows,
            "task_count": len(rows),
            "sample_count": len(durations),
            "duration_ms_avg": sum(durations) / len(durations),
            "duration_ms_p50": percentile(durations, 50.0),
            "duration_ms_p95": percentile(durations, 95.0),
            "mcp_calls": 0,
        }
    )
    return metrics


def _compute_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive recall, over-blocking, confusion, and F1 from prediction rows."""
    confusion = {truth: {pred: 0 for pred in _FOUR_STATES} for truth in _FOUR_STATES}
    for row in rows:
        truth = str(row["truth"])
        prediction = str(row["prediction"])
        if truth in confusion and prediction in confusion[truth]:
            confusion[truth][prediction] += 1

    per_class: dict[str, dict[str, float]] = {}
    for state in _FOUR_STATES:
        tp = confusion[state][state]
        fp = sum(confusion[truth][state] for truth in _FOUR_STATES) - tp
        fn = sum(confusion[state].values()) - tp
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        per_class[state] = {"precision": precision, "recall": recall, "f1": f1}

    block_total = sum(confusion["block"].values())
    non_block_total = len(rows) - block_total
    false_block = sum(confusion[truth]["block"] for truth in _FOUR_STATES if truth != "block")
    impact = [row for row in rows if row["impact"]]
    impact_non_allow = sum(1 for row in impact if row["prediction"] != "allow")

    return {
        "confusion_matrix": confusion,
        "macro_f1": sum(per_class[state]["f1"] for state in _FOUR_STATES) / len(_FOUR_STATES),
        "critical_violation_recall": per_class["block"]["recall"],
        "unsafe_allow_rate": confusion["block"]["allow"] / block_total if block_total else 0.0,
        "false_block_rate": false_block / non_block_total if non_block_total else 0.0,
        "approval_precision": per_class["approval"]["precision"],
        "approval_recall": per_class["approval"]["recall"],
        "required_test_recall": per_class["tests"]["recall"],
        "impact_recall": impact_non_allow / len(impact) if impact else 1.0,
    }


def _offline_transcript(tasks: list[dict[str, Any]], dataset: Path, revision: object) -> dict[str, Any]:
    transcript = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))
    if not isinstance(transcript, dict) or not _TRANSCRIPT_FIELDS.issubset(transcript):
        raise ValueError("recorded agent transcript is incomplete")
    if transcript["track"] != "recorded":
        raise ValueError("offline transcript must be explicitly marked as recorded")
    task = next((item for item in tasks if item["id"] == transcript["task_id"]), None)
    if task is None or transcript["revision"] != revision:
        raise ValueError("recorded agent transcript does not match dataset")
    _, diff_text = _read_diff(task, dataset)
    if transcript["diff_sha256"] != sha256(diff_text.encode()).hexdigest():
        raise ValueError("recorded agent transcript does not match fixture")
    _validate_transcript_metadata(transcript, task)
    calls = transcript["tool_calls"]
    if not isinstance(calls, list) or not calls or any(not isinstance(call, dict) for call in calls):
        raise ValueError("recorded agent transcript has no MCP calls")
    _validate_tool_calls(calls, diff_text, str(transcript["decision"]))
    return transcript


def _validate_transcript_metadata(transcript: dict[str, Any], task: dict[str, Any]) -> None:
    """Reject incomplete or self-reported metadata that cannot identify the run."""
    for field in ("agent", "model", "prompt", "decision"):
        if not isinstance(transcript.get(field), str) or not transcript[field]:
            raise ValueError(f"agent transcript has invalid {field}")
    if transcript["prompt"] != task.get("prompt"):
        raise ValueError("agent transcript prompt does not match task")
    duration = transcript.get("duration_ms")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
        raise ValueError("agent transcript has invalid duration_ms")


def _validate_tool_calls(
    calls: list[dict[str, Any]],
    diff_text: str,
    transcript_decision: str,
) -> None:
    """Replay the exact call and require recorded output and decision to match FastMCP."""
    from agents_mcp.server import mcp
    from mcp.server.fastmcp.exceptions import ToolError

    if len(calls) != 1:
        raise ValueError("agent transcript must contain exactly one MCP call")
    for call in calls:
        tool = call.get("tool")
        arguments = call.get("input")
        if tool != "bizguard.get_change_decision" or not isinstance(arguments, dict):
            raise ValueError("recorded agent transcript has an unsupported tool call")
        if arguments != {"diff_text": diff_text}:
            raise ValueError("recorded agent transcript MCP input does not match fixture")
        try:
            result = asyncio.run(mcp.call_tool("get_change_decision", arguments))
        except (ToolError, ValueError) as exc:
            raise ValueError("recorded agent transcript tool input fails MCP schema validation") from exc
        if not isinstance(result, tuple) or len(result) != 2 or not isinstance(result[1], dict):
            raise ValueError("BizGuard MCP replay returned no structured output")
        output = call.get("output")
        if not isinstance(output, dict) or output != result[1]:
            raise ValueError("recorded agent transcript output does not match MCP replay")
        decision = result[1].get("decision")
        if not isinstance(decision, str) or _MCP_DECISIONS.get(decision) != transcript_decision:
            raise ValueError("recorded agent transcript decision does not match MCP replay")


def _live_transcript(tasks: list[dict[str, Any]], dataset: Path, revision: object) -> dict[str, Any]:
    """Run a configured agent command; it must emit a complete MCP transcript JSON."""
    command = os.environ.get("BIZGUARD_LIVE_AGENT_COMMAND")
    if not command:
        raise RuntimeError("--live requires BIZGUARD_LIVE_AGENT_COMMAND to invoke the configured LLM agent")
    task_id = os.environ.get("BIZGUARD_LIVE_TASK_ID", str(tasks[0]["id"]))
    if not any(str(item["id"]) == task_id for item in tasks):
        raise ValueError(f"unknown BIZGUARD_LIVE_TASK_ID: {task_id}")
    environment = os.environ.copy()
    environment.update(
        {
            "BIZGUARD_LIVE_DATASET": str(dataset.resolve()),
            "BIZGUARD_LIVE_TASK_ID": task_id,
            "BIZGUARD_LIVE_REVISION": str(revision),
        }
    )
    completed = subprocess.run(
        shlex.split(command),
        capture_output=True,
        check=True,
        text=True,
        env=environment,
    )
    transcript = json.loads(completed.stdout)
    if not isinstance(transcript, dict) or not _TRANSCRIPT_FIELDS.issubset(transcript):
        raise ValueError("live agent did not emit a complete JSON transcript")
    if transcript["track"] != "live":
        raise ValueError("live agent transcript must be explicitly marked as live")
    if str(transcript["model"]).startswith("recorded-"):
        raise ValueError("live agent transcript cannot use a recorded model identity")
    task = next((item for item in tasks if item["id"] == transcript.get("task_id")), None)
    if task is None or transcript.get("revision") != revision:
        raise ValueError("live agent transcript does not match dataset")
    _, diff_text = _read_diff(task, dataset)
    if transcript.get("diff_sha256") != sha256(diff_text.encode()).hexdigest():
        raise ValueError("live agent transcript does not match fixture")
    _validate_transcript_metadata(transcript, task)
    calls = transcript["tool_calls"]
    if not isinstance(calls, list) or not calls or any(not isinstance(call, dict) for call in calls):
        raise ValueError("live agent transcript has no MCP calls")
    _validate_tool_calls(calls, diff_text, str(transcript["decision"]))
    return transcript


def run(dataset: Path, offline: bool) -> dict[str, Any]:
    tasks = _load_tasks(dataset)
    if not tasks:
        raise ValueError("benchmark dataset has no tasks")
    for task in tasks:
        _read_diff(task, dataset)
    revision = _dataset_revision(dataset)
    trajectory = _offline_trajectory(tasks, dataset, revision) if offline else _live_transcript(tasks, dataset, revision)
    compiler = ContextCompiler(ROOT / "fixtures" / "java-microservices", reuse_index=True)
    return {
        "dataset_revision": revision,
        "offline_notice": "Naive Baseline is a heuristic, not a real agent.",
        "baselines": [_measure(tasks, baseline, dataset, compiler, {}) for baseline in BASELINES],
        "agent_trajectory": trajectory,
    }


def _offline_trajectory(tasks: list[dict[str, Any]], dataset: Path, revision: object) -> dict[str, Any] | None:
    """Return the recorded trajectory only when it targets this dataset's tasks."""
    transcript = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))
    if not isinstance(transcript, dict):
        return None
    task_id = transcript.get("task_id")
    if not any(str(item["id"]) == str(task_id) for item in tasks):
        return None
    return _offline_transcript(tasks, dataset, revision)


def _load_tasks(dataset: Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(dataset.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
        raise ValueError("benchmark dataset has no tasks")
    tasks = cast(list[dict[str, Any]], raw["tasks"])
    truth_file = dataset.parent / "truth.yaml"
    if truth_file.is_file():
        truth_raw = yaml.safe_load(truth_file.read_text(encoding="utf-8"))
        truth_map = {
            str(item["id"]): item["truth"]
            for item in truth_raw["tasks"]
            if isinstance(item, dict) and "id" in item and "truth" in item
        }
        for task in tasks:
            task["truth"] = truth_map[str(task["id"])]
    return tasks


def _dataset_revision(dataset: Path) -> object:
    raw = yaml.safe_load(dataset.read_text(encoding="utf-8"))
    return raw.get("version") if isinstance(raw, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--transcript-out",
        type=Path,
        help="write the validated agent trajectory as a standalone replay artifact",
    )
    args = parser.parse_args()
    if args.offline == args.live:
        return 2
    output = run(args.dataset, args.offline)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    if args.transcript_out is not None:
        args.transcript_out.parent.mkdir(parents=True, exist_ok=True)
        args.transcript_out.write_text(
            json.dumps(output["agent_trajectory"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
