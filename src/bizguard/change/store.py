"""SQLite and PostgreSQL storage for immutable Context Packs."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from typing import Protocol


class ContextStore(Protocol):
    """Persistence contract for immutable Context Packs."""

    def put(self, context_id: str, payload: str, created_at: str) -> None:
        """Store a new immutable context or verify an identical retry."""

    def get(self, context_id: str) -> str | None:
        """Return one serialized Context Pack by ID."""

    def ping(self) -> bool:
        """Return whether the backing store can execute a query."""

    def close(self) -> None:
        """Release storage resources."""


class ChangeContextStore:
    """Persist JSON Context Packs without mutating a previously stored ID."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self._read_only = read_only
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        if read_only and Path(f"{path}-wal").exists():
            raise OSError("change context database has an uncheckpointed WAL")
        target = (
            f"{path.resolve().as_uri()}?mode=ro&immutable=1"
            if read_only
            else str(path)
        )
        self._connection = sqlite3.connect(
            target,
            timeout=30,
            check_same_thread=False,
            uri=read_only,
        )
        if not read_only:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=30000")
        if not read_only:
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS change_context "
                "(id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL)"
            )

    def put(self, context_id: str, payload: str, created_at: str) -> None:
        if self._read_only:
            raise PermissionError("change context store is read-only")
        with self._lock:
            existing = self.get(context_id)
            if existing is not None and existing != payload:
                raise ValueError("change context IDs are immutable")
            self._connection.execute(
                "INSERT OR IGNORE INTO change_context (id, payload, created_at) VALUES (?, ?, ?)",
                (context_id, payload, created_at),
            )
            self._connection.commit()

    def get(self, context_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM change_context WHERE id = ?", (context_id,)
            ).fetchone()
        return str(row[0]) if row else None

    def ping(self) -> bool:
        with self._lock:
            row = self._connection.execute("SELECT 1").fetchone()
        return bool(row and row[0] == 1)

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class PostgresChangeContextStore:
    """Shared immutable Context Pack store for multi-instance deployments."""

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
    ) -> None:
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - exercised without production extra
            raise RuntimeError("PostgreSQL support requires the production dependency extra") from exc
        if min_pool_size < 0 or max_pool_size < max(1, min_pool_size):
            raise ValueError("invalid PostgreSQL pool size")
        self._pool = ConnectionPool(
            database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            kwargs={"autocommit": True},
            open=True,
        )
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "CREATE TABLE IF NOT EXISTS bizguard_change_context ("
                " id TEXT PRIMARY KEY,"
                " payload TEXT NOT NULL,"
                " created_at TIMESTAMPTZ NOT NULL"
                ")"
            )

    def put(self, context_id: str, payload: str, created_at: str) -> None:
        with self._pool.connection() as connection, connection.transaction():
            connection.execute(
                "INSERT INTO bizguard_change_context (id, payload, created_at) "
                "VALUES (%s, %s, %s::timestamptz) ON CONFLICT (id) DO NOTHING",
                (context_id, payload, created_at),
            )
            row = connection.execute(
                "SELECT payload FROM bizguard_change_context WHERE id = %s FOR UPDATE",
                (context_id,),
            ).fetchone()
            if row is None or str(row[0]) != payload:
                raise ValueError("change context IDs are immutable")

    def get(self, context_id: str) -> str | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM bizguard_change_context WHERE id = %s",
                (context_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def ping(self) -> bool:
        with self._pool.connection() as connection:
            row = connection.execute("SELECT 1").fetchone()
        return bool(row and row[0] == 1)

    def close(self) -> None:
        self._pool.close()
