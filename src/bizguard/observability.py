"""Safe operational telemetry; never retain conversations or document bodies."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

_SENSITIVE_FIELD = re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization|credential|conversation)")
_SENSITIVE_ASSIGNMENT = re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+")
_SENSITIVE_TOKEN = re.compile(r"(?i)\b(sk-[a-z0-9_-]{8,}|ghp_[a-z0-9]{8,}|eyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})")


def _redact_field(key: str, value: str) -> str:
    """Redact sensitive field names and credential-shaped values."""
    if key == "conversation":
        return "[REDACTED]"
    if _SENSITIVE_FIELD.search(key):
        return "[REDACTED]"
    value = _SENSITIVE_ASSIGNMENT.sub("[REDACTED]", value)
    return _SENSITIVE_TOKEN.sub("[REDACTED]", value)


class AuditEvent(BaseModel):
    """A schema-typed, replayable audit event."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action: str
    change_context_id: str
    trace_id: str | None = None
    details: dict[str, str] = Field(default_factory=dict)

    def json_line(self) -> str:
        return self.model_dump_json()


class AuditTrail:
    """Append redacted workflow audit metadata without retaining document bodies."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def add(self, action: str, context_id: str, trace_id: str | None = None, **details: str) -> AuditEvent:
        safe = {key: _redact_field(key, value) for key, value in details.items()}
        event = AuditEvent(action=action, change_context_id=context_id, trace_id=trace_id, details=safe)
        self.events.append(event)
        return event

    def events_for(self, context_id: str) -> list[AuditEvent]:
        return [event for event in self.events if event.change_context_id == context_id]


def audit_json(path: Path, change_context_id: str, event: str, details: dict[str, str]) -> None:
    """Append a metadata-only audit event with credential-shaped values removed."""
    safe = {key: _redact_field(key, value) for key, value in details.items()}
    record = {"at": datetime.now(timezone.utc).isoformat(), "change_context_id": change_context_id, "event": event, "details": safe}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def percentile(values: list[float], percent: float) -> float:
    """Return the linear-interpolated percentile of measured samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (percent / 100.0) * (len(ordered) - 1)
    lower = int(rank)
    upper = lower + 1
    weight = rank - lower
    if upper >= len(ordered):
        return ordered[-1]
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def export_metrics(records: list[dict[str, object]]) -> dict[str, object]:
    """Export count, decision distribution, unknown rate, and latency percentiles."""
    durations = [_as_float(record.get("duration_ms", 0.0)) for record in records]
    decisions = [str(record["decision"]) for record in records if record.get("decision") is not None]
    unknown_count = sum(1 for record in records if record.get("unknown") is True)
    sample_count = len(records)
    return {
        "count": float(sample_count),
        "decision_distribution": {value: decisions.count(value) for value in sorted(set(decisions))},
        "unknown_rate": (unknown_count / sample_count) if sample_count else 0.0,
        "duration_ms_p50": percentile(durations, 50.0),
        "duration_ms_p95": percentile(durations, 95.0),
        "sample_count": sample_count,
        "low_sample": sample_count < 5,
    }


def _as_float(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0
