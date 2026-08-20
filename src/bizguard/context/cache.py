"""In-memory Context Pack cache; keys include every isolation boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import TYPE_CHECKING

from .staleness import Clock, is_stale, utc_now

if TYPE_CHECKING:
    from .compiler import ContextPack


@dataclass(frozen=True)
class CacheKey:
    """Identify a cached Context Pack across all isolation boundaries."""

    task: str
    repos: tuple[str, ...]
    revisions: tuple[tuple[str, str], ...]
    principal: str
    index_version: str
    token_budget: int

    @classmethod
    def create(
        cls, task: str, repos: list[str], revisions: dict[str, str], principal: str, index_version: str,
        token_budget: int = 2000,
    ) -> "CacheKey":
        return cls(task, tuple(sorted(repos)), tuple(sorted(revisions.items())), principal, index_version, token_budget)

    @property
    def digest(self) -> str:
        raw = json.dumps(self.__dict__, ensure_ascii=False, sort_keys=True, default=list)
        return sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class CachedContext:
    """Store a Context Pack with its creation and revision metadata."""

    pack: "ContextPack"
    created_at: datetime
    revisions: dict[str, str]


class ContextCache:
    """Cache Context Packs in memory with revision and TTL validation."""

    def __init__(self, ttl: timedelta = timedelta(minutes=10), now: Clock = utc_now) -> None:
        self._ttl, self._now = ttl, now
        self._items: dict[str, CachedContext] = {}

    def get(self, key: CacheKey, revisions: dict[str, str]) -> "ContextPack | None":
        entry = self._items.get(key.digest)
        if entry is None or is_stale(entry.created_at, self._ttl, entry.revisions, revisions, self._now):
            return None
        return entry.pack

    def put(self, key: CacheKey, pack: "ContextPack", revisions: dict[str, str]) -> None:
        self._items[key.digest] = CachedContext(pack, self._now(), dict(revisions))
