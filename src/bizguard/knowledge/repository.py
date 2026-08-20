"""SQLite-backed knowledge repository with FTS5 indexes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bizguard.knowledge.models import KnowledgeEntry


class KnowledgeRepository:
    """Store and search knowledge entries in SQLite."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS knowledge (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(id UNINDEXED, title, content)"
        )

    def close(self) -> None:
        self.connection.close()

    def put(self, entry: KnowledgeEntry) -> None:
        payload = entry.model_dump_json()
        self.connection.execute(
            "INSERT OR REPLACE INTO knowledge VALUES (?, ?)", (entry.id, payload)
        )
        self.connection.execute("DELETE FROM knowledge_fts WHERE id = ?", (entry.id,))
        self.connection.execute(
            "INSERT INTO knowledge_fts (id, title, content) VALUES (?, ?, ?)",
            (entry.id, entry.title, entry.content),
        )
        self.connection.commit()

    def all(self) -> list[KnowledgeEntry]:
        return [
            KnowledgeEntry.model_validate_json(row["payload"])
            for row in self.connection.execute("SELECT payload FROM knowledge")
        ]

    def bm25(self, query: str) -> dict[str, float]:
        try:
            rows = self.connection.execute(
                "SELECT id, bm25(knowledge_fts) AS score FROM knowledge_fts WHERE knowledge_fts MATCH ?",
                (query,),
            )
        except sqlite3.OperationalError:
            return {}
        return {str(row["id"]): -float(row["score"]) for row in rows}

    @classmethod
    def memory(cls) -> "KnowledgeRepository":
        return cls(Path(":memory:"))
