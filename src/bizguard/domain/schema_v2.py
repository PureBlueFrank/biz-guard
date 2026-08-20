"""Version-two policy lifecycle schema, deliberately separate from MVP v1."""

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bizguard.domain.enums import PolicyMode


class PolicyLifecycle(StrEnum):
    """Enumerate supported policy lifecycle states."""

    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class PolicyV2(BaseModel):
    """Define lifecycle metadata for a version-two policy."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    lifecycle: PolicyLifecycle
    mode: PolicyMode
    grandfathered_evidence: str = Field(min_length=1)
    evidence_uri: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9+.-]*://")


class InvariantsV2(BaseModel):
    """Define the version-two policy invariants document."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    policies: list[PolicyV2] = Field(min_length=1)


def load_invariants_v2(path: Path) -> InvariantsV2:
    """Load v2 policy lifecycle metadata without rewriting the v1 policy file."""
    try:
        return InvariantsV2.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, ValidationError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid v2 invariants file {path}: {exc}") from exc
