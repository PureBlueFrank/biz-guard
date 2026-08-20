"""Revision and TTL staleness checks with an injectable clock."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)


def is_stale(
    created_at: datetime,
    ttl: timedelta,
    cached_revisions: dict[str, str],
    requested_revisions: dict[str, str],
    now: Clock = utc_now,
) -> bool:
    """A pack is reusable only when it is fresh and exactly revision-pinned."""
    return now() - created_at > ttl or cached_revisions != requested_revisions
