"""Context Pack cache and staleness acceptance tests without wall-clock sleeps."""

from datetime import UTC, datetime, timedelta

import pytest

from bizguard.context.cache import CacheKey, ContextCache
from bizguard.context.staleness import is_stale


def test_base_revision_change_marks_context_stale() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert is_stale(now, timedelta(minutes=10), {"coupon-core": "a"}, {"coupon-core": "b"}, lambda: now)


def test_injected_clock_marks_expired_context_stale_without_sleep() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    assert is_stale(created, timedelta(seconds=1), {"coupon-core": "a"}, {"coupon-core": "a"}, lambda: created + timedelta(seconds=2))


@pytest.mark.parametrize("changed", ["task", "repos", "revisions", "principal", "index_version", "token_budget"])
def test_cache_key_contains_each_isolation_boundary(changed: str) -> None:
    first = CacheKey.create("status", ["coupon-core"], {"coupon-core": "a"}, "engineering", "v1")
    alternatives = {
        "task": CacheKey.create("other", ["coupon-core"], {"coupon-core": "a"}, "engineering", "v1"),
        "repos": CacheKey.create("status", ["merchant-service"], {"coupon-core": "a"}, "engineering", "v1"),
        "revisions": CacheKey.create("status", ["coupon-core"], {"coupon-core": "b"}, "engineering", "v1"),
        "principal": CacheKey.create("status", ["coupon-core"], {"coupon-core": "a"}, "audit", "v1"),
        "index_version": CacheKey.create("status", ["coupon-core"], {"coupon-core": "a"}, "engineering", "v2"),
        "token_budget": CacheKey.create("status", ["coupon-core"], {"coupon-core": "a"}, "engineering", "v1", 800),
    }
    second = alternatives[changed]
    assert first.digest != second.digest


def test_cache_rejects_changed_revisions() -> None:
    cache = ContextCache(now=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    key = CacheKey.create("status", ["coupon-core"], {"coupon-core": "a"}, "engineering", "v1")
    cache.put(key, object(), {"coupon-core": "a"})  # type: ignore[arg-type]
    assert cache.get(key, {"coupon-core": "b"}) is None
