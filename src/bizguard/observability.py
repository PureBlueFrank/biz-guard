"""Safe operational telemetry; never retain conversations or document bodies."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

_SENSITIVE = re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+")


class AuditEvent(BaseModel):
    """In-memory audit event used by the approval workflow."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action: str
    change_context_id: str
    details: dict[str, str] = Field(default_factory=dict)

    def json_line(self) -> str:
        return self.model_dump_json()


class AuditTrail:
    """Append redacted workflow audit metadata without retaining document bodies."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def add(self, action: str, context_id: str, **details: str) -> AuditEvent:
        safe = {key: _SENSITIVE.sub("[REDACTED]", value) for key, value in details.items()}
        event = AuditEvent(action=action, change_context_id=context_id, details=safe)
        self.events.append(event)
        return event


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
