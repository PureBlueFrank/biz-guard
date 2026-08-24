"""Safe ingestion: generated content remains a draft until an owner publishes it."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from bizguard.knowledge.models import KnowledgeEntry, KnowledgeStatus
from bizguard.knowledge.repository import KnowledgeRepository

_INJECTION = re.compile(
    r"(?i)(?:"
    r"\bignore (?:all |previous )?instructions\b|"
    r"\bdisregard all (?:prior )?(?:instructions|directions)\b|"
    r"\bforget (?:all )?(?:your )?(?:rules|instructions)\b|"
    r"\bdeveloper[ -]mode\b|"
    r"\breveal (?:your )?(?:hidden )?(?:system )?prompt\b|"
    r"\bsystem prompt (?:override|instructions?)\b|"
    r"\bact as\b|\bjailbreak\b|"
    r"忽略(?:(?:之前|前面|上面)(?:的)?所有|之前|前面|上面|所有)(?:的)?(?:指令|规则|提示)|"
    r"无视(?:上面|前面|所有|之前)?(?:的)?(?:指令|规则)|"
    r"开发者模式|(?:输出|显示|泄露)(?:你的)?(?:系统)?提示词|越狱"
    r")"
)


def knowledge_content_digest(directory: Path) -> str:
    """Hash governed knowledge filenames and bytes for cache and approval binding."""
    signature = tuple(
        (path.name, sha256(path.read_bytes()).hexdigest())
        for path in sorted(directory.glob("*.md"))
    )
    return sha256(json.dumps(signature, sort_keys=True).encode("utf-8")).hexdigest()


def ingest_file(
    path: Path,
    repository: KnowledgeRepository,
    generated: bool = False,
    *,
    quarantine_on_rejection: bool = True,
) -> KnowledgeEntry:
    """Validate and store one Markdown record, refusing instruction-like content."""
    raw = path.read_text(encoding="utf-8")
    if _INJECTION.search(raw):
        safe_name = Path(path.name).name
        if safe_name in {"", ".", ".."}:
            raise ValueError("knowledge path has no safe quarantine filename")
        if not quarantine_on_rejection:
            raise ValueError(f"prompt-injection content rejected: {safe_name}")
        quarantine = path.parent / "quarantine"
        quarantine.mkdir(exist_ok=True)
        destination = quarantine / safe_name
        if destination.parent.resolve() != quarantine.resolve():
            raise ValueError("knowledge quarantine destination escapes its directory")
        destination.write_text(raw, encoding="utf-8")
        raise ValueError(f"prompt-injection content quarantined: {safe_name}")
    metadata, body = _front_matter(raw)
    metadata["content"] = body.strip()
    if generated:
        metadata["status"] = KnowledgeStatus.DRAFT
    entry = KnowledgeEntry.model_validate(metadata)
    _validate_location(path, entry.status)
    repository.put(entry)
    return entry


def ingest_directory(
    directory: Path,
    repository: KnowledgeRepository,
    *,
    quarantine_on_rejection: bool = True,
) -> list[KnowledgeEntry]:
    """Validate and store every Markdown knowledge record in a directory."""
    return [
        ingest_file(
            path,
            repository,
            quarantine_on_rejection=quarantine_on_rejection,
        )
        for path in sorted(directory.glob("*.md"))
    ]


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
