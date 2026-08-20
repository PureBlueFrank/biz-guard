"""Policy registry: deterministic validators, ownership, and lifecycle metadata."""

from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from .lifecycle import PolicyMode


class PolicyDefinition(BaseModel):
    id: str
    validator: str
    scope: str
    severity: str
    owner: str
    remediation: str
    required_tests: list[str] = Field(default_factory=list)
    mode: PolicyMode = PolicyMode.DRAFT


def load_registry(path: Path) -> list[PolicyDefinition]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("policies"), list):
        raise ValueError("policy registry must contain a policies list")
    return [PolicyDefinition.model_validate(item) for item in raw["policies"]]
