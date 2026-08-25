"""SQLite and PostgreSQL approval persistence with an append-only audit log."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from typing import Callable, Protocol


ApprovalMutation = Callable[[str | None], tuple[str, str, list[str]]]


class ApprovalStore(Protocol):
    """Persistence seam for approval workflows.

    The business layer depends on this protocol so tests and production can
    swap the storage backend without coupling to a global connection.
    """

    def get(self, change_context_id: str, policy_revision: str, approver_set: str) -> str | None:
        """Return the serialized approval record for the idempotency key, if any."""

    def put(
        self, change_context_id: str, policy_revision: str, approver_set: str, payload: str, updated_at: str
    ) -> None:
        """Persist an approval record, replacing any record with the same key."""

    def append_event(self, change_context_id: str, event_json: str) -> None:
        """Append one audit event; never overwrites history."""

    def events(self, change_context_id: str) -> list[str]:
        """Return the append-only audit event payloads for a context."""

    def get_by_context(self, change_context_id: str, policy_revision: str) -> str | None:
        """Return the most recent record for a context, regardless of approver set."""

    def mutate(
        self,
        change_context_id: str,
        policy_revision: str,
        approver_set: str,
        operation: ApprovalMutation,
    ) -> str:
        """Atomically update one approval and append its audit events."""

    def close(self) -> None:
        """Release the underlying storage."""

    def ping(self) -> bool:
        """Return whether the backing store can execute a query."""


class SqliteApprovalStore:
    """Default local SQLite implementation of :class:`ApprovalStore`."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self._read_only = read_only
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        if read_only and Path(f"{path}-wal").exists():
            raise OSError("approval database has an uncheckpointed WAL")
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
                "CREATE TABLE IF NOT EXISTS approvals ("
                " change_context_id TEXT NOT NULL,"
                " policy_revision TEXT NOT NULL,"
                " approver_set TEXT NOT NULL,"
                " payload TEXT NOT NULL,"
                " updated_at TEXT NOT NULL,"
                " PRIMARY KEY (change_context_id, policy_revision, approver_set)"
                ")"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS audit ("
                " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                " change_context_id TEXT NOT NULL,"
                " payload TEXT NOT NULL"
                ")"
            )
            self._connection.commit()

    def get(self, change_context_id: str, policy_revision: str, approver_set: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM approvals "
                "WHERE change_context_id = ? AND policy_revision = ? AND approver_set = ?",
                (change_context_id, policy_revision, approver_set),
            ).fetchone()
        return str(row[0]) if row else None

    def put(
        self, change_context_id: str, policy_revision: str, approver_set: str, payload: str, updated_at: str
    ) -> None:
        if self._read_only:
            raise PermissionError("approval store is read-only")
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO approvals "
                "(change_context_id, policy_revision, approver_set, payload, updated_at) VALUES (?, ?, ?, ?, ?)",
                (change_context_id, policy_revision, approver_set, payload, updated_at),
            )
            self._connection.commit()

    def append_event(self, change_context_id: str, event_json: str) -> None:
        if self._read_only:
            raise PermissionError("approval store is read-only")
        with self._lock:
            self._connection.execute(
                "INSERT INTO audit (change_context_id, payload) VALUES (?, ?)",
                (change_context_id, event_json),
            )
            self._connection.commit()

    def events(self, change_context_id: str) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM audit WHERE change_context_id = ? ORDER BY seq",
                (change_context_id,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def get_by_context(self, change_context_id: str, policy_revision: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM approvals "
                "WHERE change_context_id = ? AND policy_revision = ? ORDER BY updated_at DESC LIMIT 1",
                (change_context_id, policy_revision),
            ).fetchone()
        return str(row[0]) if row else None

    def mutate(
        self,
        change_context_id: str,
        policy_revision: str,
        approver_set: str,
        operation: ApprovalMutation,
    ) -> str:
        if self._read_only:
            raise PermissionError("approval store is read-only")
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT payload FROM approvals "
                    "WHERE change_context_id = ? AND policy_revision = ? AND approver_set = ?",
                    (change_context_id, policy_revision, approver_set),
                ).fetchone()
                payload, updated_at, events = operation(str(row[0]) if row else None)
                self._connection.execute(
                    "INSERT OR REPLACE INTO approvals "
                    "(change_context_id, policy_revision, approver_set, payload, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (change_context_id, policy_revision, approver_set, payload, updated_at),
                )
                self._connection.executemany(
                    "INSERT INTO audit (change_context_id, payload) VALUES (?, ?)",
                    [(change_context_id, event) for event in events],
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        return payload

    def ping(self) -> bool:
        with self._lock:
            row = self._connection.execute("SELECT 1").fetchone()
        return bool(row and row[0] == 1)

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class PostgresApprovalStore:
    """Multi-instance approval store with transaction-scoped advisory locking."""

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
                "CREATE TABLE IF NOT EXISTS bizguard_approvals ("
                " change_context_id TEXT NOT NULL,"
                " policy_revision TEXT NOT NULL,"
                " approver_set TEXT NOT NULL,"
                " payload TEXT NOT NULL,"
                " updated_at TIMESTAMPTZ NOT NULL,"
                " PRIMARY KEY (change_context_id, policy_revision, approver_set)"
                ")"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS bizguard_approval_audit ("
                " seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"
                " change_context_id TEXT NOT NULL,"
                " payload TEXT NOT NULL"
                ")"
            )

    def get(self, change_context_id: str, policy_revision: str, approver_set: str) -> str | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM bizguard_approvals "
                "WHERE change_context_id = %s AND policy_revision = %s AND approver_set = %s",
                (change_context_id, policy_revision, approver_set),
            ).fetchone()
        return str(row[0]) if row else None

    def put(
        self,
        change_context_id: str,
        policy_revision: str,
        approver_set: str,
        payload: str,
        updated_at: str,
    ) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                "INSERT INTO bizguard_approvals "
                "(change_context_id, policy_revision, approver_set, payload, updated_at) "
                "VALUES (%s, %s, %s, %s, %s::timestamptz) "
                "ON CONFLICT (change_context_id, policy_revision, approver_set) DO UPDATE SET "
                "payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at",
                (change_context_id, policy_revision, approver_set, payload, updated_at),
            )

    def append_event(self, change_context_id: str, event_json: str) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                "INSERT INTO bizguard_approval_audit (change_context_id, payload) VALUES (%s, %s)",
                (change_context_id, event_json),
            )

    def events(self, change_context_id: str) -> list[str]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM bizguard_approval_audit "
                "WHERE change_context_id = %s ORDER BY seq",
                (change_context_id,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def get_by_context(self, change_context_id: str, policy_revision: str) -> str | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT payload FROM bizguard_approvals "
                "WHERE change_context_id = %s AND policy_revision = %s "
                "ORDER BY updated_at DESC LIMIT 1",
                (change_context_id, policy_revision),
            ).fetchone()
        return str(row[0]) if row else None

    def mutate(
        self,
        change_context_id: str,
        policy_revision: str,
        approver_set: str,
        operation: ApprovalMutation,
    ) -> str:
        lock_key = f"{change_context_id}\x1f{policy_revision}\x1f{approver_set}"
        with self._pool.connection() as connection, connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))
            row = connection.execute(
                "SELECT payload FROM bizguard_approvals "
                "WHERE change_context_id = %s AND policy_revision = %s AND approver_set = %s",
                (change_context_id, policy_revision, approver_set),
            ).fetchone()
            payload, updated_at, events = operation(str(row[0]) if row else None)
            connection.execute(
                "INSERT INTO bizguard_approvals "
                "(change_context_id, policy_revision, approver_set, payload, updated_at) "
                "VALUES (%s, %s, %s, %s, %s::timestamptz) "
                "ON CONFLICT (change_context_id, policy_revision, approver_set) DO UPDATE SET "
                "payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at",
                (change_context_id, policy_revision, approver_set, payload, updated_at),
            )
            if events:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        "INSERT INTO bizguard_approval_audit (change_context_id, payload) "
                        "VALUES (%s, %s)",
                        [(change_context_id, event) for event in events],
                    )
        return payload

    def ping(self) -> bool:
        with self._pool.connection() as connection:
            row = connection.execute("SELECT 1").fetchone()
        return bool(row and row[0] == 1)

    def close(self) -> None:
        self._pool.close()
