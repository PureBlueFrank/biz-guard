"""SQLite storage for immutable Context Packs, scoped to a local run directory."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock


class ChangeContextStore:
    """Persist JSON Context Packs without mutating a previously stored ID."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=30000")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS change_context "
            "(id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL)"
        )

    def put(self, context_id: str, payload: str, created_at: str) -> None:
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

    def close(self) -> None:
        with self._lock:
            self._connection.close()
