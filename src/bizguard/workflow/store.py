"""SQLite-backed persistence for approval records and their append-only audit log."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from typing import Protocol


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

    def close(self) -> None:
        """Release the underlying storage."""


class SqliteApprovalStore:
    """Default local SQLite implementation of :class:`ApprovalStore`."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=30000")
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
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO approvals "
                "(change_context_id, policy_revision, approver_set, payload, updated_at) VALUES (?, ?, ?, ?, ?)",
                (change_context_id, policy_revision, approver_set, payload, updated_at),
            )
            self._connection.commit()

    def append_event(self, change_context_id: str, event_json: str) -> None:
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

    def close(self) -> None:
        with self._lock:
            self._connection.close()
