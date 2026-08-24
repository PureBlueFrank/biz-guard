"""SQLite storage for immutable Context Packs, scoped to a local run directory."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock


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

    def close(self) -> None:
        with self._lock:
            self._connection.close()
