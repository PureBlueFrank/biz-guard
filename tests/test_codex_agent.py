"""Offline contract tests for the opt-in Codex CLI live-agent harness."""

from __future__ import annotations

from copy import deepcopy
import asyncio
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

import scripts.codex_agent as agent
from agents_mcp.server import mcp


ROOT = Path(__file__).parents[1]
DATASET = ROOT / "bench/ablations/tasks.yaml"


def _codex_stdout(
    diff_text: str,
    *,
    call_diff: str | None = None,
    final_decision: str = "block",
) -> str:
    arguments = {
        "diff_text": diff_text if call_diff is None else call_diff,
    }
    result = asyncio.run(mcp.call_tool("get_change_decision", arguments))
    assert isinstance(result, tuple) and isinstance(result[1], dict)
    tool_output = result[1]
    events = [
        {"type": "thread.started", "thread_id": "codex-thread-1"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {
                "id": "item-1",
                "type": "mcp_tool_call",
                "server": "bizguard",
                "tool": "get_change_decision",
                "arguments": arguments,
                "status": "completed",
                "error": None,
                "result": {
                    "structuredContent": tool_output,
                },
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item-2",
                "type": "agent_message",
                "text": json.dumps(
                    {"decision": final_decision, "rationale": "BizGuard blocked the patch."}
                ),
            },
        },
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 100, "output_tokens": 20},
        },
    ]
    return "\n".join(json.dumps(item) for item in events)


def test_live_agent_launches_codex_with_real_stdio_mcp_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diff_text = agent._load_task(DATASET, "critical-ledger-1")[3]
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert kwargs["stdin"] is subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 0, stdout=_codex_stdout(diff_text), stderr="")

    monkeypatch.setattr("scripts.codex_agent.subprocess.run", fake_run)

    transcript = agent.run_live_agent(
        dataset=DATASET,
        task_id="critical-ledger-1",
        model="codex-test-model",
    )

    command = commands[0]
    assert command[:4] == ["codex", "--ask-for-approval", "never", "exec"]
    assert "--json" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "mcp_servers.bizguard.required=true" in command
    assert 'mcp_servers.bizguard.enabled_tools=["get_change_decision"]' in command
    assert transcript["track"] == "live"
    assert transcript["agent"] == "codex-cli"
    assert transcript["model"] == "codex-test-model"
    assert transcript["decision"] == "block"
    assert transcript["codex_thread_id"] == "codex-thread-1"
    assert transcript["tool_calls"][0]["input"] == {"diff_text": diff_text}  # type: ignore[index]


def test_live_agent_rejects_codex_mutation_of_frozen_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diff_text = agent._load_task(DATASET, "critical-ledger-1")[3]
    stdout = _codex_stdout(diff_text, call_diff="changed")
    monkeypatch.setattr(
        "scripts.codex_agent.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout=stdout, stderr=""
        ),
    )

    with pytest.raises(ValueError, match="changed the frozen diff"):
        agent.run_live_agent(
            dataset=DATASET,
            task_id="critical-ledger-1",
            model="codex-test-model",
        )


def test_live_agent_rejects_decision_that_disagrees_with_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diff_text = agent._load_task(DATASET, "critical-ledger-1")[3]
    monkeypatch.setattr(
        "scripts.codex_agent.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=_codex_stdout(diff_text, final_decision="allow"),
            stderr="",
        ),
    )

    with pytest.raises(ValueError, match="does not match"):
        agent.run_live_agent(
            dataset=DATASET,
            task_id="critical-ledger-1",
            model="codex-test-model",
        )


def test_live_agent_rejects_tampered_mcp_output(monkeypatch: pytest.MonkeyPatch) -> None:
    diff_text = agent._load_task(DATASET, "critical-ledger-1")[3]
    events = [json.loads(line) for line in _codex_stdout(diff_text).splitlines()]
    events[2]["item"]["result"]["structuredContent"]["findings"] = []
    stdout = "\n".join(json.dumps(item) for item in events)
    monkeypatch.setattr(
        "scripts.codex_agent.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout=stdout, stderr=""
        ),
    )

    with pytest.raises(ValueError, match="does not match FastMCP replay"):
        agent.run_live_agent(
            dataset=DATASET,
            task_id="critical-ledger-1",
            model="codex-test-model",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("server", "untrusted", "unsupported MCP tool"),
        ("tool", "prepare_change", "unsupported MCP tool"),
        ("status", "failed", "did not complete successfully"),
        ("error", {"message": "failure"}, "did not complete successfully"),
    ],
)
def test_live_agent_rejects_wrong_or_failed_mcp_event(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    diff_text = agent._load_task(DATASET, "critical-ledger-1")[3]
    events = [json.loads(line) for line in _codex_stdout(diff_text).splitlines()]
    events[2]["item"][field] = value
    stdout = "\n".join(json.dumps(item) for item in events)
    monkeypatch.setattr(
        "scripts.codex_agent.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout=stdout, stderr=""
        ),
    )

    with pytest.raises(ValueError, match=message):
        agent.run_live_agent(
            dataset=DATASET,
            task_id="critical-ledger-1",
            model="codex-test-model",
        )


@pytest.mark.parametrize("mode", ["missing", "duplicate"])
def test_live_agent_requires_exactly_one_mcp_call(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    diff_text = agent._load_task(DATASET, "critical-ledger-1")[3]
    events = [json.loads(line) for line in _codex_stdout(diff_text).splitlines()]
    if mode == "missing":
        events.pop(2)
    else:
        duplicate = deepcopy(events[2])
        duplicate["item"]["id"] = "item-extra"
        events.insert(3, duplicate)
    stdout = "\n".join(json.dumps(item) for item in events)
    monkeypatch.setattr(
        "scripts.codex_agent.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout=stdout, stderr=""
        ),
    )

    with pytest.raises(ValueError, match="exactly one MCP tool call"):
        agent.run_live_agent(
            dataset=DATASET,
            task_id="critical-ledger-1",
            model="codex-test-model",
        )
