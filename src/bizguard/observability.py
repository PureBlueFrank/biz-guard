"""Safe operational telemetry; never retain conversations or document bodies."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_SENSITIVE = re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+")


def audit_json(path: Path, change_context_id: str, event: str, details: dict[str, str]) -> None:
    """Append a metadata-only audit event with credential-shaped values removed."""
    safe = {key: _SENSITIVE.sub("[REDACTED]", value) for key, value in details.items() if key != "conversation"}
    record = {"at": datetime.now(timezone.utc).isoformat(), "change_context_id": change_context_id, "event": event, "details": safe}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def export_metrics(records: list[dict[str, float]]) -> dict[str, float]:
    """Export count and simple latency aggregates from measured records."""
    values = [item.get("duration_ms", 0.0) for item in records]
    return {"count": float(len(values)), "duration_ms_total": sum(values), "duration_ms_p50": sorted(values)[len(values) // 2] if values else 0.0}
