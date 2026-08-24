"""Idempotent local hook manifest generation."""

import json
from pathlib import Path

from bizguard.connectors import HOOK_COMMAND


def install(directory: Path) -> Path:
    """Write the BizGuard agent hook manifest to a directory."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "bizguard-hook.json"
    target.write_text(json.dumps({"command": HOOK_COMMAND}) + "\n", encoding="utf-8")
    return target
