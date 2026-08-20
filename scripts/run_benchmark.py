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

from bizguard.context.compiler import ContextCompiler
from bizguard.decision import Decision, evaluate_change
from bizguard.decision.v2 import DecisionInput, DecisionState, FindingV2, decide
from bizguard.knowledge.ingest import ingest_directory
from bizguard.knowledge.models import SearchRequest, SearchResult
from bizguard.knowledge.repository import KnowledgeRepository
from bizguard.knowledge.search import HybridSearch, LocalVectorAdapter


BASELINES = ("Naive Baseline", "Rules Only", "RAG Only", "Context", "Full")
ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = ROOT / "bench" / "ablations" / "agent_transcript.json"
_OUTCOMES = {
    DecisionState.ALLOW: "allow",
    DecisionState.ALLOW_WITH_TESTS: "tests",
    DecisionState.REQUIRE_APPROVAL: "approval",
    DecisionState.BLOCK: "block",
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


def _context(task: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Compile an actual Context Pack and decide only from its emitted evidence."""
    repositories = task.get("repositories", ["coupon-core"])
    revisions = task.get("base_revisions", {"coupon-core": "benchmark-base"})
    if not isinstance(repositories, list) or not isinstance(revisions, dict):
        raise ValueError(f"task {task.get('id')} has invalid Context inputs")
    pack = ContextCompiler(ROOT / "fixtures" / "java-microservices").compile(
        str(task["prompt"]),
        [str(item) for item in repositories],
        {str(key): str(value) for key, value in revisions.items()},
    )
    pack_data = pack.model_dump(mode="json")
    if pack.unknowns or pack.required_approvers:
        return "approval", pack_data
    if pack.required_tests:
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


def _full(task: dict[str, Any], diff_text: str) -> str:
    """Run policy validators then feed their real result into Decision v2."""
    policy = evaluate_change(diff_text)
    _, pack = _context(task)
    violated = policy.decision is Decision.BLOCK
    unknown = policy.decision is Decision.CHECK_INCOMPLETE
    finding = FindingV2(
        id="policy-pipeline",
        severity="critical" if violated else "medium",
        effect="AST policy evaluation",
        remediation="resolve policy findings",
        confidence=1.0,
        violated=violated,
        critical_unknown=unknown,
        public_contract=bool(task.get("public_contract", False)),
    )
    result = decide(
        DecisionInput(
            findings=[finding],
            tests_passed=bool(task["tests_passed"]),
            required_tests=cast(list[str], [str(item["id"]) for item in pack["required_tests"]]),
            owners=cast(list[str], pack["required_approvers"]) + cast(list[str], task.get("owners", [])),
            version_known=not unknown,
        )
    )
    return _OUTCOMES[result.decision]


def _predict(task: dict[str, Any], baseline: str, dataset: Path) -> str:
    """Dispatch to the component named by the ablation; labels are never consulted."""
    _, diff_text = _read_diff(task, dataset)
    if baseline == "Rules Only":
        return _rules(diff_text)
    if baseline == "RAG Only":
        return _rag(diff_text)
    if baseline == "Context":
        return _context(task)[0]
    if baseline == "Naive Baseline":
        return _naive_baseline(diff_text)
    if baseline == "Full":
        return _full(task, diff_text)
    raise ValueError(f"unknown baseline: {baseline}")


def _measure(tasks: list[dict[str, Any]], baseline: str, dataset: Path) -> dict[str, Any]:
    started = time.perf_counter()
    rows = [
        {"task_id": task["id"], "prediction": _predict(task, baseline, dataset), "truth": task["truth"]}
        for task in tasks
    ]
    critical = [row for row in rows if row["truth"] == "block"]
    impact = [task for task in tasks if bool(task["impact"])]
    predictions = {row["task_id"]: row["prediction"] for row in rows}
    return {
        "baseline": baseline,
        "tasks": rows,
        "task_count": len(rows),
        "critical_violation_recall": sum(row["prediction"] == "block" for row in critical) / len(critical),
        "unsafe_allow_rate": sum(row["prediction"] == "allow" for row in critical) / len(critical),
        "impact_recall": sum(predictions[str(task["id"])] != "allow" for task in impact) / len(impact),
        "cost_units": sum(len(_read_diff(task, dataset)[1]) for task in tasks),
        "duration_ms": (time.perf_counter() - started) * 1000,
    }


def _offline_transcript(tasks: list[dict[str, Any]], dataset: Path, revision: object) -> dict[str, Any]:
    transcript = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))
    required = {"agent", "model", "prompt", "bizguard_version", "revision", "task_id", "decision", "duration_ms", "tool_calls", "diff_sha256"}
    if not isinstance(transcript, dict) or not required.issubset(transcript):
        raise ValueError("recorded agent transcript is incomplete")
    task = next((item for item in tasks if item["id"] == transcript["task_id"]), None)
    if task is None or transcript["revision"] != revision:
        raise ValueError("recorded agent transcript does not match dataset")
    _, diff_text = _read_diff(task, dataset)
    if transcript["diff_sha256"] != sha256(diff_text.encode()).hexdigest():
        raise ValueError("recorded agent transcript does not match fixture")
    calls = transcript["tool_calls"]
    if not isinstance(calls, list) or not calls or any(not isinstance(call, dict) for call in calls):
        raise ValueError("recorded agent transcript has no MCP calls")
    _validate_tool_calls(calls)
    return transcript


def _validate_tool_calls(calls: list[dict[str, Any]]) -> None:
    """Replay transcript calls through FastMCP so recorded inputs remain schema-valid."""
    from agents_mcp.server import mcp
    from mcp.server.fastmcp.exceptions import ToolError

    for call in calls:
        tool = call.get("tool")
        arguments = call.get("input")
        if tool != "bizguard.validate_patch" or not isinstance(arguments, dict):
            raise ValueError("recorded agent transcript has an unsupported tool call")
        try:
            asyncio.run(mcp.call_tool("validate_patch", arguments))
        except (ToolError, ValueError) as exc:
            raise ValueError("recorded agent transcript tool input fails MCP schema validation") from exc


def _live_transcript(tasks: list[dict[str, Any]], dataset: Path, revision: object) -> dict[str, Any]:
    """Run a configured agent command; it must emit a complete MCP transcript JSON."""
    command = os.environ.get("BIZGUARD_LIVE_AGENT_COMMAND")
    if not command:
        raise RuntimeError("--live requires BIZGUARD_LIVE_AGENT_COMMAND to invoke the configured LLM agent")
    completed = subprocess.run(shlex.split(command), capture_output=True, check=True, text=True)
    transcript = json.loads(completed.stdout)
    if not isinstance(transcript, dict):
        raise ValueError("live agent did not emit a JSON transcript")
    task = next((item for item in tasks if item["id"] == transcript.get("task_id")), None)
    if task is None or transcript.get("revision") != revision:
        raise ValueError("live agent transcript does not match dataset")
    _, diff_text = _read_diff(task, dataset)
    if transcript.get("diff_sha256") != sha256(diff_text.encode()).hexdigest():
        raise ValueError("live agent transcript does not match fixture")
    return transcript


def run(dataset: Path, offline: bool) -> dict[str, Any]:
    raw = yaml.safe_load(dataset.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
        raise ValueError("benchmark dataset has no tasks")
    tasks = cast(list[dict[str, Any]], raw["tasks"])
    if len(tasks) != 12:
        raise ValueError("benchmark requires frozen 12 tasks")
    for task in tasks:
        _read_diff(task, dataset)
    revision = raw.get("version")
    trajectory = _offline_transcript(tasks, dataset, revision) if offline else _live_transcript(tasks, dataset, revision)
    return {
        "dataset_revision": revision,
        "offline_notice": "Naive Baseline is a heuristic, not a real agent.",
        "baselines": [_measure(tasks, baseline, dataset) for baseline in BASELINES],
        "agent_trajectory": trajectory,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.offline == args.live:
        return 2
    output = run(args.dataset, args.offline)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
