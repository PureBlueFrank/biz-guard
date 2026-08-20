"""Governed knowledge records used by the Phase 2 retrieval service."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KnowledgeStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    STALE = "stale"


class SecurityLabel(StrEnum):
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    PUBLIC = "public"


class KnowledgeEntry(BaseModel):
    """One immutable, evidence-bearing knowledge item."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    expires_at: datetime | None = None
    security_label: SecurityLabel = SecurityLabel.INTERNAL
    acl: list[str] = Field(default_factory=lambda: ["engineering"])
    status: KnowledgeStatus
    policy_ids: list[str] = Field(default_factory=list)
    evidence_uri: str = Field(min_length=1)

    @model_validator(mode="after")
    def published_items_need_owner_and_acl(self) -> "KnowledgeEntry":
        if self.status is KnowledgeStatus.PUBLISHED and (not self.owner or not self.acl):
            raise ValueError("published knowledge requires an owner and ACL")
        return self

    def is_fresh(self, now: datetime) -> bool:
        return self.expires_at is None or self.expires_at >= now


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    caller_roles: list[str]
    scope: str
    revision: str
    limit: int = Field(default=5, ge=1, le=20)
    now: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CandidateTrace(BaseModel):
    id: str
    bm25_score: float | None = None
    vector_score: float | None = None
    rerank_score: float | None = None
    elimination_reason: str | None = None


class SearchResult(BaseModel):
    entries: list[KnowledgeEntry]
    traces: list[CandidateTrace]
    mandatory_policy_ids: list[str]
    semantic_channel: str
    embedding_model: str | None = None
    embedding_cache_version: str | None = None
