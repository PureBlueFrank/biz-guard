"""Agent connector installation, idempotency, and hook execution tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from bizguard.connectors import connect


ROOT = Path(__file__).parents[1]


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "bizguard.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_connect_dry_run_writes_nothing(tmp_path: Path) -> None:
    result = connect(
        "codex",
        tmp_path,
        dry_run=True,
        identity="coupon_platform",
        roles=["engineering", "security"],
    )
    assert result["dry_run"] is True
    assert not (tmp_path / ".codex" / "bizguard.json").exists()
    assert "codex mcp add bizguard" in str(result["content"])
    assert f"BIZGUARD_REPOSITORY_ROOT={tmp_path}" in str(result["content"])
    assert "BIZGUARD_CALLER_IDENTITY=coupon_platform" in str(result["content"])
    assert "BIZGUARD_CALLER_ROLES=engineering,security" in str(result["content"])


def test_connect_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registered = False

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal registered
        if command[:4] == ["codex", "mcp", "get", "bizguard"]:
            server = ROOT / "agents_mcp/server.py"
            payload = {
                "transport": {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": [str(server)],
                    "env": {"BIZGUARD_REPOSITORY_ROOT": str(tmp_path)},
                }
            }
            return subprocess.CompletedProcess(
                command,
                0 if registered else 1,
                stdout=json.dumps(payload) if registered else "",
                stderr="",
            )
        assert command[:4] == ["codex", "mcp", "add", "bizguard"]
        registered = True
        return subprocess.CompletedProcess(command, 0, stdout="Added MCP server\n", stderr="")

    monkeypatch.setattr("bizguard.connectors.subprocess.run", fake_run)
    first = connect("codex", tmp_path)
    second = connect("codex", tmp_path)
    assert first["changed"] is True
    assert second["changed"] is False
    assert not (tmp_path / ".codex" / "bizguard.json").exists()


def test_connect_refuses_to_overwrite_conflicting_codex_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bizguard.connectors.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "transport": {
                        "type": "stdio",
                        "command": "different-python",
                        "args": [],
                        "env": None,
                    }
                }
            ),
            stderr="",
        ),
    )
    with pytest.raises(RuntimeError, match="conflicting"):
        connect("codex", tmp_path)


def test_connect_preserves_existing_user_config(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text('{"permissions": {"allow": ["Read"]}}\n', encoding="utf-8")
    connect("claude-code", tmp_path)
    content = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    assert content["permissions"] == {"allow": ["Read"]}
    assert "hooks" in content


def test_hook_command_executes_in_a_git_repository(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("world\n", encoding="utf-8")

    result = _run_cli(["hook", "--repository", str(tmp_path)], tmp_path)
    assert result.returncode in (0, 1)
    payload = json.loads(result.stdout)
    assert "decision" in payload


def test_doctor_reports_failed_for_missing_prerequisites(tmp_path: Path) -> None:
    result = _run_cli(["doctor", "--repository", str(tmp_path), "--json"], tmp_path)
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["checks"]["policy"] == "failed"
    assert result.returncode == 1


def test_connect_rejects_unknown_agent() -> None:
    import pytest

    from bizguard.connectors import connector_for

    with pytest.raises(ValueError):
        connector_for("unknown-agent")
