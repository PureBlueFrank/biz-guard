"""Idempotent local hook manifest generation."""

from pathlib import Path


def install(directory: Path) -> Path:
    """Write the BizGuard agent hook manifest to a directory."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "bizguard-hook.json"
    target.write_text('{"command":"python -m bizguard.ci.check"}\n', encoding="utf-8")
    return target
