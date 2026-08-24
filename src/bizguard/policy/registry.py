"""Policy registry: deterministic validators, ownership, and lifecycle metadata."""

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from typing import Literal

from pydantic import BaseModel, Field

from .lifecycle import PolicyMode


class PolicyDefinition(BaseModel):
    """Describe a registered policy and its enforcement metadata."""

    id: str
    validator: str
    scope: str
    severity: str
    owner: str
    remediation: str
    required_tests: list[str] = Field(default_factory=list)
    mode: PolicyMode = PolicyMode.DRAFT
    precision: Literal["high", "medium", "low"] = "medium"


def load_registry(path: Path) -> list[PolicyDefinition]:
    """Load and validate policy definitions from a YAML registry."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("policies"), list):
        raise ValueError("policy registry must contain a policies list")
    return [PolicyDefinition.model_validate(item) for item in raw["policies"]]
