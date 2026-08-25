"""Typed read-only view of the frozen semantic catalog."""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field


class Domain(BaseModel):
    """Represent a business domain in the semantic catalog."""

    id: str
    name: str


class Capability(BaseModel):
    """Represent an owned capability and its repositories."""

    id: str
    name: str
    owner: str
    repositories: list[str]
    graph: bool = False


class Owner(BaseModel):
    """Represent a catalog owner and its repository scope."""

    id: str
    name: str
    repositories: list[str]


class Entity(BaseModel):
    """Represent a catalog entity bound to a canonical symbol."""

    id: str
    capability: str
    repository: str
    canonical_id: str


class State(BaseModel):
    """Represent a named state associated with a catalog entity."""

    id: str
    entity: str
    value: str


class Invariant(BaseModel):
    """Represent an owned business invariant and its source."""

    id: str
    capability: str
    owner: str
    statement: str
    source_id: str


class Policy(BaseModel):
    """Represent the policy metadata that enforces an invariant."""

    id: str
    capability: str
    owner: str
    invariant: str
    severity: str
    mode: str


class CatalogRequiredTest(BaseModel):
    """Describe a governed test required by a catalog policy."""

    id: str
    capability: str
    owner: str
    policy: str
    command: str
    repository: str
    evidence: list[dict[str, object]] = Field(default_factory=list)


class SemanticCatalog(BaseModel):
    """Provide a validated, immutable view of semantic catalog data."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    revision: str
    capabilities: list[Capability]
    owners: list[Owner]
    entities: list[Entity]
    states: list[State]
    invariants: list[Invariant]
    policies: list[Policy]
    required_tests: list[CatalogRequiredTest]

    def capability(self, identifier: str) -> Capability:
        for item in self.capabilities:
            if item.id == identifier:
                return item
        raise KeyError(identifier)

    def owner(self, identifier: str) -> Owner:
        for item in self.owners:
            if item.id == identifier:
                return item
        raise KeyError(identifier)


def load_catalog(path: Path) -> SemanticCatalog:
    """Load and validate a semantic catalog from YAML."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("semantic catalog must be a mapping")
    allowed = set(SemanticCatalog.model_fields)
    return SemanticCatalog.model_validate(
        {key: value for key, value in raw.items() if key in allowed}
    )
