"""Thin agent-connector adapters that render config around the shared CLI."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


HOOK_COMMAND = "python -m bizguard.cli hook --repository ."


class AgentConnector:
    """Render one agent's hook/MCP config without duplicating decision logic."""

    agent: str = ""
    target_relpath: str = ""

    def target_path(self, repository: Path) -> Path:
        return repository / self.target_relpath

    def render(self) -> str:
        raise NotImplementedError

    def merge(self, existing: str, rendered: str) -> str:
        raise NotImplementedError


class ClaudeCodeConnector(AgentConnector):
    """Claude Code PostToolUse hook config in ``.claude/settings.json``."""

    agent = "claude-code"
    target_relpath = ".claude/settings.json"

    def render(self) -> str:
        return _dump(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit|Write|MultiEdit",
                            "hooks": [{"type": "command", "command": HOOK_COMMAND}],
                        }
                    ]
                }
            }
        )

    def merge(self, existing: str, rendered: str) -> str:
        base = _load_dict(existing)
        if base is None:
            return rendered
        incoming = _load_dict(rendered)
        if incoming is None:
            return existing
        hooks: dict[str, Any] = base.setdefault("hooks", {})
        for event, entries in incoming.get("hooks", {}).items():
            hooks.setdefault(event, [])
            for entry in entries:
                if entry not in hooks[event]:
                    hooks[event].append(entry)
        return _dump(base)


class CodexConnector(AgentConnector):
    """Describe the Codex MCP registration managed by the supported Codex CLI."""

    agent = "codex"
    target_relpath = ".codex/config.toml"

    def render(self) -> str:
        return "Codex MCP configuration is managed by `codex mcp add`.\n"

    def merge(self, existing: str, rendered: str) -> str:
        base = _load_dict(existing)
        if base is None:
            return rendered
        incoming = _load_dict(rendered)
        if incoming is None:
            return existing
        servers: dict[str, Any] = base.setdefault("mcp_servers", {})
        servers.update(incoming.get("mcp_servers", {}))
        base["hook"] = incoming.get("hook", base.get("hook"))
        return _dump(base)


def connector_for(agent: str) -> AgentConnector:
    """Return the connector registered for the named agent."""
    for connector in (ClaudeCodeConnector(), CodexConnector()):
        if connector.agent == agent:
            return connector
    raise ValueError(f"unsupported agent connector: {agent}")


def connect(
    agent: str,
    repository: Path,
    *,
    dry_run: bool = False,
    identity: str | None = None,
    roles: list[str] | None = None,
) -> dict[str, object]:
    """Render and optionally write one agent connector, idempotently."""
    if agent == "codex":
        return _connect_codex(
            repository,
            dry_run=dry_run,
            identity=identity,
            roles=roles,
        )
    connector = connector_for(agent)
    target = connector.target_path(repository)
    rendered = connector.render()

    existing: str | None = None
    if target.is_file():
        existing = target.read_text(encoding="utf-8")
        if HOOK_COMMAND in existing:
            return {
                "agent": agent,
                "target": str(target),
                "changed": False,
                "dry_run": dry_run,
                "content": existing,
            }
    merged = connector.merge(existing, rendered) if existing is not None else rendered

    if dry_run:
        return {
            "agent": agent,
            "target": str(target),
            "changed": merged != (existing or ""),
            "dry_run": True,
            "content": merged,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(merged, encoding="utf-8")
    return {
        "agent": agent,
        "target": str(target),
        "changed": True,
        "dry_run": False,
        "content": merged,
    }


def _connect_codex(
    repository: Path,
    *,
    dry_run: bool,
    identity: str | None,
    roles: list[str] | None,
) -> dict[str, object]:
    """Register BizGuard using Codex's supported user-level MCP configuration command."""
    server = Path(__file__).parents[3] / "agents_mcp" / "server.py"
    environment = {"BIZGUARD_REPOSITORY_ROOT": str(repository.resolve())}
    if identity:
        environment["BIZGUARD_CALLER_IDENTITY"] = identity
    if roles:
        environment["BIZGUARD_CALLER_ROLES"] = ",".join(roles)
    command = [
        "codex",
        "mcp",
        "add",
        "bizguard",
    ]
    for key, value in environment.items():
        command.extend(["--env", f"{key}={value}"])
    command.extend([
        "--",
        sys.executable,
        str(server),
    ])
    target = Path.home() / ".codex" / "config.toml"
    content = shlex.join(command)
    if dry_run:
        return {
            "agent": "codex",
            "target": str(target),
            "changed": True,
            "dry_run": True,
            "content": content,
        }
    existing = subprocess.run(
        ["codex", "mcp", "get", "bizguard", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if existing.returncode == 0:
        try:
            payload = json.loads(existing.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("codex returned an unreadable BizGuard MCP configuration") from exc
        transport = payload.get("transport") if isinstance(payload, dict) else None
        matches = (
            isinstance(transport, dict)
            and transport.get("type") == "stdio"
            and transport.get("command") == sys.executable
            and transport.get("args") == [str(server)]
            and transport.get("env") == environment
        )
        if not matches:
            raise RuntimeError(
                "Codex already has a conflicting 'bizguard' MCP entry; inspect it with "
                "`codex mcp get bizguard --json` before replacing it"
            )
        return {
            "agent": "codex",
            "target": str(target),
            "changed": False,
            "dry_run": False,
            "content": json.dumps(payload, sort_keys=True),
        }
    added = subprocess.run(command, capture_output=True, text=True, check=False)
    if added.returncode != 0:
        raise RuntimeError(added.stderr.strip() or "codex mcp add failed")
    return {
        "agent": "codex",
        "target": str(target),
        "changed": True,
        "dry_run": False,
        "content": added.stdout or content,
    }


def _load_dict(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _dump(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2) + "\n"
