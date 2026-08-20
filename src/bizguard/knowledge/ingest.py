"""Safe ingestion: generated content remains a draft until an owner publishes it."""

from __future__ import annotations

import re
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from bizguard.knowledge.models import KnowledgeEntry, KnowledgeStatus
from bizguard.knowledge.repository import KnowledgeRepository

_INJECTION = re.compile(r"(?i)(ignore (all |previous )?instructions|system prompt|act as|jailbreak)")


def ingest_file(path: Path, repository: KnowledgeRepository, generated: bool = False) -> KnowledgeEntry:
    """Validate and store one Markdown record, refusing instruction-like content."""
    raw = path.read_text(encoding="utf-8")
    if _INJECTION.search(raw):
        quarantine = path.parent / "quarantine"
        quarantine.mkdir(exist_ok=True)
        (quarantine / path.name).write_text(raw, encoding="utf-8")
        raise ValueError(f"prompt-injection content quarantined: {path.name}")
    metadata, body = _front_matter(raw)
    metadata["content"] = body.strip()
    if generated:
        metadata["status"] = KnowledgeStatus.DRAFT
    entry = KnowledgeEntry.model_validate(metadata)
    _validate_location(path, entry.status)
    repository.put(entry)
    return entry


def ingest_directory(directory: Path, repository: KnowledgeRepository) -> list[KnowledgeEntry]:
    return [ingest_file(path, repository) for path in sorted(directory.glob("*.md"))]


def _front_matter(raw: str) -> tuple[dict[str, object], str]:
    if not raw.startswith("---\n"):
        raise ValueError("knowledge must start with YAML front matter")
    front, marker, body = raw[4:].partition("\n---\n")
    if not marker:
        raise ValueError("knowledge front matter is not closed")
    data = yaml.safe_load(front)
    if not isinstance(data, dict):
        raise ValueError("knowledge front matter must be a mapping")
    return data, body


def _validate_location(path: Path, status: KnowledgeStatus) -> None:
    parent = path.parent.name
    if parent == "candidates" and status is not KnowledgeStatus.DRAFT:
        raise ValueError("knowledge/candidates only accepts draft entries")
    if parent == "published" and status not in {KnowledgeStatus.PUBLISHED, KnowledgeStatus.STALE}:
        raise ValueError("knowledge/published only accepts owner-confirmed published entries")
