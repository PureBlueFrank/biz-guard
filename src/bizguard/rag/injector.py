"""The full-text retrieval baseline used by BizGuard decisions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from pydantic import BaseModel, ConfigDict, ValidationError

from bizguard.diff_parser import ParsedDiff


class Contract(BaseModel):
    """One field-impact entry from the loaded contract registry."""

    model_config = ConfigDict(extra="forbid")

    id: str
    field: str
    service: str
    capability: str
    owner: str
    source: str
    policy_ids: list[str]


class KnowledgeDocument(BaseModel):
    """A knowledge Markdown document with its parsed front matter."""

    id: str
    title: str
    content: str
    path: str


class KnowledgeFrontMatter(BaseModel):
    """Required metadata for a knowledge source."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    type: str
    scope: str
    source: str
    owner: str
    policy_ids: list[str]


class RetrievalEvidence(BaseModel):
    """Contracts and knowledge documents retrieved for a change."""

    contract_ids: list[str]
    knowledge_document_ids: list[str]
    full_text: str


def load_contract_registry(path: Path) -> list[Contract]:
    """Load the version-one contract registry needed by the injector."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or loaded.get("version") != 1:
        raise ValueError("contract registry must contain version: 1")
    contracts = loaded.get("contracts")
    if not isinstance(contracts, list):
        raise ValueError("contract registry must contain a contracts list")
    try:
        parsed = [Contract.model_validate(contract) for contract in contracts]
    except ValidationError as exc:
        raise ValueError(f"invalid contract registry: {exc}") from exc
    identifiers = [contract.id for contract in parsed]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("contract registry ids must be unique")
    if any(not contract.policy_ids for contract in parsed):
        raise ValueError("each contract must reference at least one policy id")
    return parsed


def load_knowledge_documents(directory: Path) -> list[KnowledgeDocument]:
    """Load every knowledge document, retaining its full source text for evidence."""
    documents: list[KnowledgeDocument] = []
    for path in sorted(directory.glob("*.md")):
        source = path.read_text(encoding="utf-8")
        metadata, body = _split_front_matter(source, path)
        try:
            front_matter = KnowledgeFrontMatter.model_validate(metadata)
        except ValidationError as exc:
            raise ValueError(f"invalid knowledge front matter in {path}: {exc}") from exc
        documents.append(
            KnowledgeDocument(
                id=front_matter.id, title=front_matter.title, content=body, path=path.as_posix()
            )
        )
    identifiers = [document.id for document in documents]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("knowledge document ids must be unique")
    return documents


def inject_full_text(
    parsed_diff: ParsedDiff,
    registry: Sequence[Contract | Mapping[str, object]],
    knowledge_documents: Sequence[KnowledgeDocument],
) -> RetrievalEvidence:
    """Inject all knowledge text and only contracts related to changed source paths.

    This is deliberately the sole decision-path retrieval mechanism.  The small,
    fixed knowledge corpus is included in full once a related contract is found.
    """
    changed_paths = {
        path
        for parsed_file in parsed_diff.files
        for path in (parsed_file.old_path, parsed_file.new_path)
        if path is not None
    }
    contracts = [_as_contract(contract) for contract in registry]
    matched_contracts = [contract for contract in contracts if contract.source in changed_paths]
    if not matched_contracts or not knowledge_documents:
        return RetrievalEvidence(contract_ids=[], knowledge_document_ids=[], full_text="")

    contract_text = "\n".join(
        f"## Contract: {contract.id}\n{json.dumps(contract.model_dump(), ensure_ascii=False, sort_keys=True)}"
        for contract in matched_contracts
    )
    knowledge_text = "\n\n".join(
        f"## Knowledge: {document.id}\n{document.content.strip()}" for document in knowledge_documents
    )
    return RetrievalEvidence(
        contract_ids=[contract.id for contract in matched_contracts],
        knowledge_document_ids=[document.id for document in knowledge_documents],
        full_text=f"{contract_text}\n\n{knowledge_text}",
    )


def _as_contract(contract: Contract | Mapping[str, object]) -> Contract:
    if isinstance(contract, Contract):
        return contract
    return Contract.model_validate(contract)


def _split_front_matter(source: str, path: Path) -> tuple[dict[str, object], str]:
    if not source.startswith("---\n"):
        raise ValueError(f"knowledge document {path} must start with YAML front matter")
    raw_metadata, separator, body = source[4:].partition("\n---\n")
    if not separator:
        raise ValueError(f"knowledge document {path} has unterminated YAML front matter")
    metadata = yaml.safe_load(raw_metadata)
    if not isinstance(metadata, dict):
        raise ValueError(f"knowledge document {path} front matter must be a mapping")
    return metadata, body
