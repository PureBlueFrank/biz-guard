"""Run one frozen BizGuard benchmark task with a real Codex CLI MCP agent."""

from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any, cast

import yaml  # type: ignore[import-untyped]


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "bench/ablations/tasks.yaml"
MCP_TOOL_NAME = "bizguard.validate_patch"
_DECISIONS = {"ALLOW": "allow", "BLOCK": "block", "CHECK_INCOMPLETE": "approval"}
_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["allow", "tests", "approval", "block"]},
        "rationale": {"type": "string"},
    },
    "required": ["decision", "rationale"],
    "additionalProperties": False,
}


def _load_task(dataset: Path, task_id: str) -> tuple[dict[str, Any], str, Path, str]:
    """Load one frozen benchmark task and its exact local diff fixture."""
    raw = yaml.safe_load(dataset.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
        raise ValueError("benchmark dataset has no tasks")
    tasks = cast(list[dict[str, Any]], raw["tasks"])
    task = next((item for item in tasks if str(item.get("id")) == task_id), None)
    if task is None:
        raise ValueError(f"benchmark task does not exist: {task_id}")
    diff_value = task.get("diff")
    if not isinstance(diff_value, str):
        raise ValueError("benchmark task has no diff fixture")
    diff_path = (dataset.parent / diff_value).resolve()
    if not diff_path.is_file():
        raise ValueError(f"benchmark diff is unreadable: {diff_path}")
    return task, str(raw.get("version")), diff_path, diff_path.read_text(encoding="utf-8")


def _prompt(task: dict[str, Any], diff_path: Path, diff_text: str) -> str:
    """Build a frozen prompt that requires one exact BizGuard MCP call."""
    return (
        "This is a read-only benchmark run. Evaluate the local fixture below. You MUST call "
        "the bizguard MCP server's validate_patch tool exactly once with the complete, "
        "unmodified diff as diff_text. Do not invoke BizGuard through the shell and do not "
        "modify files. After the MCP result, return only the requested JSON decision. Map "
        "ALLOW to allow, BLOCK to block, and CHECK_INCOMPLETE to approval.\n\n"
        f"Task ID: {task['id']}\nTask: {task['prompt']}\nFixture: {diff_path}\n"
        f"Diff SHA-256: {sha256(diff_text.encode()).hexdigest()}\n"
        f"<frozen_diff>\n{diff_text}</frozen_diff>"
    )


def _codex_command(
    *,
    codex_command: str,
    model: str | None,
    schema_path: Path,
    prompt: str,
) -> list[str]:
    """Construct a read-only Codex invocation with only BizGuard's validator exposed."""
    command = [
        *shlex.split(codex_command),
        "--ask-for-approval",
        "never",
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--json",
        "--color",
        "never",
        "--sandbox",
        "read-only",
        "-C",
        str(ROOT),
        "--output-schema",
        str(schema_path),
        "-c",
        f"mcp_servers.bizguard.command={json.dumps(sys.executable)}",
        "-c",
        'mcp_servers.bizguard.args=["-m","agents_mcp.server"]',
        "-c",
        f"mcp_servers.bizguard.cwd={json.dumps(str(ROOT))}",
        "-c",
        "mcp_servers.bizguard.required=true",
        "-c",
        'mcp_servers.bizguard.enabled_tools=["validate_patch"]',
    ]
    if model:
        command.extend(["--model", model])
    command.append(prompt)
    return command


def _json_object(value: object, label: str) -> dict[str, Any]:
    """Normalize a dictionary or JSON string into one object."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Codex {label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Codex {label} is not a JSON object")
    return cast(dict[str, Any], value)


def _structured_tool_output(result: object) -> dict[str, Any]:
    """Extract FastMCP structured content from a completed Codex tool item."""
    payload = _json_object(result, "MCP result")
    for key in ("structuredContent", "structured_content"):
        structured = payload.get(key)
        if isinstance(structured, dict):
            return cast(dict[str, Any], structured)
    content = payload.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                try:
                    parsed = json.loads(item["text"])
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return cast(dict[str, Any], parsed)
    if isinstance(payload.get("decision"), str):
        return payload
    raise ValueError("Codex MCP result has no structured BizGuard payload")


def _replay_tool_call(call: dict[str, Any]) -> None:
    """Require the recorded Codex MCP output to equal a fresh FastMCP replay."""
    from agents_mcp.server import mcp
    from mcp.server.fastmcp.exceptions import ToolError

    arguments = call.get("input")
    output = call.get("output")
    if not isinstance(arguments, dict) or not isinstance(output, dict):
        raise ValueError("Codex transcript has no replayable MCP call")
    try:
        result = asyncio.run(mcp.call_tool("validate_patch", arguments))
    except (ToolError, ValueError) as exc:
        raise ValueError("Codex MCP call fails FastMCP schema replay") from exc
    if not isinstance(result, tuple) or len(result) != 2 or not isinstance(result[1], dict):
        raise ValueError("BizGuard MCP replay returned no structured output")
    if output != result[1]:
        raise ValueError("Codex MCP output does not match FastMCP replay")


def _parse_events(stdout: str, diff_text: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Validate Codex JSONL and return thread metadata, call, and final decision."""
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        events.append(_json_object(line, "JSONL event"))
    thread = next((item for item in events if item.get("type") == "thread.started"), None)
    if thread is None or not isinstance(thread.get("thread_id"), str):
        raise ValueError("Codex JSONL has no thread.started event")
    completed_items = [
        cast(dict[str, Any], event["item"])
        for event in events
        if event.get("type") == "item.completed" and isinstance(event.get("item"), dict)
    ]
    mcp_items = [
        cast(dict[str, Any], event["item"])
        for event in events
        if event.get("type") in {"item.started", "item.completed"}
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "mcp_tool_call"
    ]
    call_ids = {item.get("id") for item in mcp_items}
    if len(call_ids) != 1:
        raise ValueError("Codex must attempt exactly one MCP tool call")
    calls = [item for item in completed_items if item.get("type") == "mcp_tool_call"]
    if len(calls) != 1:
        raise ValueError("Codex must complete exactly one MCP tool call")
    call = calls[0]
    server = call.get("server") or call.get("server_name")
    tool = call.get("tool") or call.get("tool_name")
    if server != "bizguard" or tool != "validate_patch":
        raise ValueError("Codex selected an unsupported MCP tool")
    if call.get("status") != "completed" or call.get("error") is not None:
        raise ValueError("Codex MCP tool call did not complete successfully")
    arguments = _json_object(call.get("arguments"), "MCP arguments")
    if arguments != {"diff_text": diff_text}:
        raise ValueError("Codex changed the frozen diff before validation")
    output = _structured_tool_output(call.get("result"))
    decision = output.get("decision")
    if not isinstance(decision, str) or decision not in _DECISIONS:
        raise ValueError("BizGuard MCP returned an unsupported decision")
    messages = [item for item in completed_items if item.get("type") == "agent_message"]
    if not messages or not isinstance(messages[-1].get("text"), str):
        raise ValueError("Codex JSONL has no final agent message")
    final = _json_object(messages[-1]["text"], "final message")
    if final.get("decision") != _DECISIONS[decision]:
        raise ValueError("Codex final decision does not match the BizGuard MCP result")
    usage = next(
        (event.get("usage") for event in reversed(events) if event.get("type") == "turn.completed"),
        None,
    )
    normalized_call = {
        "tool": MCP_TOOL_NAME,
        "input": arguments,
        "output": output,
        "codex_item_id": call.get("id"),
    }
    metadata = {"event_count": len(events), "usage": usage}
    return cast(str, thread["thread_id"]), normalized_call, {"final": final, **metadata}


def run_live_agent(
    *,
    dataset: Path,
    task_id: str,
    model: str,
    codex_command: str = "codex",
    timeout_seconds: float = 180.0,
) -> dict[str, object]:
    """Run Codex against one local fixture and emit a replayable live transcript."""
    task, revision, diff_path, diff_text = _load_task(dataset, task_id)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="bizguard-codex-") as temp_dir:
        schema_path = Path(temp_dir) / "decision.schema.json"
        schema_path.write_text(json.dumps(_OUTPUT_SCHEMA), encoding="utf-8")
        command = _codex_command(
            codex_command=codex_command,
            model=model,
            schema_path=schema_path,
            prompt=_prompt(task, diff_path, diff_text),
        )
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=True,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Codex CLI is not installed or not on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Codex live-agent run timed out") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Codex live-agent run failed with exit code {exc.returncode}") from exc
    thread_id, tool_call, metadata = _parse_events(completed.stdout, diff_text)
    _replay_tool_call(tool_call)
    final = cast(dict[str, Any], metadata.pop("final"))
    return {
        "track": "live",
        "agent": "codex-cli",
        "model": model,
        "prompt": str(task["prompt"]),
        "bizguard_version": version("bizguard"),
        "revision": revision,
        "task_id": task_id,
        "decision": final["decision"],
        "rationale": final["rationale"],
        "duration_ms": (time.perf_counter() - started) * 1000,
        "diff_sha256": sha256(diff_text.encode()).hexdigest(),
        "tool_calls": [tool_call],
        "codex_thread_id": thread_id,
        "codex_event_count": metadata["event_count"],
        "usage": metadata["usage"],
    }


def main() -> int:
    """Read live-track settings and print one Codex transcript as JSON."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(os.environ.get("BIZGUARD_LIVE_DATASET", DEFAULT_DATASET)),
    )
    parser.add_argument(
        "--task-id",
        default=os.environ.get("BIZGUARD_LIVE_TASK_ID", "critical-ledger-1"),
    )
    parser.add_argument("--model", default=os.environ.get("BIZGUARD_CODEX_MODEL") or None)
    parser.add_argument(
        "--codex-command",
        default=os.environ.get("BIZGUARD_CODEX_COMMAND", "codex"),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.environ.get("BIZGUARD_CODEX_TIMEOUT_SECONDS", "180")),
    )
    arguments = parser.parse_args()
    if not arguments.model:
        parser.error("--model or BIZGUARD_CODEX_MODEL is required for auditable model identity")
    transcript = run_live_agent(
        dataset=arguments.dataset,
        task_id=arguments.task_id,
        model=arguments.model,
        codex_command=arguments.codex_command,
        timeout_seconds=arguments.timeout_seconds,
    )
    print(json.dumps(transcript, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
