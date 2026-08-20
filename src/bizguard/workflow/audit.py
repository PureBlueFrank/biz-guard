"""Append-only, redacted audit events."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

_SECRET = re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+")


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action: str
    change_context_id: str
    details: dict[str, str] = Field(default_factory=dict)

    def json_line(self) -> str:
        return self.model_dump_json()


class AuditTrail:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def add(self, action: str, context_id: str, **details: str) -> AuditEvent:
        safe = {key: _SECRET.sub("[REDACTED]", value) for key, value in details.items()}
        event = AuditEvent(action=action, change_context_id=context_id, details=safe)
        self.events.append(event)
        return event
