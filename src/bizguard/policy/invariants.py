"""Policy invariant schemas and YAML loading."""

from pathlib import Path

import yaml  # type: ignore[import-untyped]

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PolicyLoadError(ValueError):
    """Raised when the invariant YAML document violates its schema."""


class RequiredCall(BaseModel):
    """A required call, its order, and its required argument."""

    model_config = ConfigDict(extra="forbid")

    before: str
    call: str
    argument: str


class TransactionContext(BaseModel):
    """The enclosing transaction requirement for an invariant."""

    model_config = ConfigDict(extra="forbid")

    decorator: str


class Target(BaseModel):
    """The Python symbol an invariant can inspect deterministically."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    file: str
    class_name: str = Field(alias="class")
    function: str


class Invariant(BaseModel):
    """A YAML-defined deterministic business invariant."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    scope: str
    target: Target
    required_call: RequiredCall
    transaction_context: TransactionContext
    evidence_refs: list[str]


def load_invariants(path: Path) -> list[Invariant]:
    """Load a version-one invariant file and reject malformed policy documents."""
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or loaded.get("version") != 1:
            raise PolicyLoadError("policy file must contain version: 1")
        raw_invariants = loaded.get("invariants")
        if not isinstance(raw_invariants, list):
            raise PolicyLoadError("policy file must contain an invariants list")
        invariants = [Invariant.model_validate(item) for item in raw_invariants]
    except (OSError, ValidationError, yaml.YAMLError) as exc:
        raise PolicyLoadError(f"invalid policy file {path}: {exc}") from exc
    identifiers = [invariant.id for invariant in invariants]
    if len(set(identifiers)) != len(identifiers):
        raise PolicyLoadError("invariant ids must be unique")
    for invariant in invariants:
        if not invariant.evidence_refs:
            raise PolicyLoadError(f"invariant {invariant.id} must include evidence references")
        if any(":" not in reference for reference in invariant.evidence_refs):
            raise PolicyLoadError(f"invariant {invariant.id} contains an invalid evidence reference")
    _validate_cross_file_references(path, invariants)
    return invariants


def _validate_cross_file_references(path: Path, invariants: list[Invariant]) -> None:
    project_root = path.parent.parent
    try:
        registry = yaml.safe_load((project_root / "registry" / "contracts.yaml").read_text(encoding="utf-8"))
        if not isinstance(registry, dict) or not isinstance(registry.get("contracts"), list):
            raise PolicyLoadError("contract registry has no contracts list")
        contract_ids = {
            item["id"]
            for item in registry["contracts"]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        registry_policy_ids = {
            policy_id
            for item in registry["contracts"]
            if isinstance(item, dict) and isinstance(item.get("policy_ids"), list)
            for policy_id in item["policy_ids"]
            if isinstance(policy_id, str)
        }
        knowledge_ids, knowledge_policy_ids = _knowledge_references(project_root / "knowledge")
    except (OSError, yaml.YAMLError, PolicyLoadError) as exc:
        raise PolicyLoadError(f"unable to validate cross-file Policy references: {exc}") from exc
    invariant_ids = {invariant.id for invariant in invariants}
    unknown_policy_ids = (registry_policy_ids | knowledge_policy_ids) - invariant_ids
    if unknown_policy_ids:
        raise PolicyLoadError(f"unknown Policy IDs referenced outside invariants: {sorted(unknown_policy_ids)}")
    for invariant in invariants:
        for reference in invariant.evidence_refs:
            kind, identifier = reference.split(":", maxsplit=1)
            if kind == "registry" and identifier not in contract_ids:
                raise PolicyLoadError(f"unknown registry evidence ID: {identifier}")
            if kind == "knowledge" and identifier not in knowledge_ids:
                raise PolicyLoadError(f"unknown knowledge evidence ID: {identifier}")


def _knowledge_references(directory: Path) -> tuple[set[str], set[str]]:
    identifiers: set[str] = set()
    policy_ids: set[str] = set()
    for knowledge_file in directory.glob("*.md"):
        source = knowledge_file.read_text(encoding="utf-8")
        raw_front_matter, separator, _ = source[4:].partition("\n---\n")
        if not source.startswith("---\n") or not separator:
            raise PolicyLoadError(f"invalid knowledge front matter: {knowledge_file}")
        metadata = yaml.safe_load(raw_front_matter)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("id"), str):
            raise PolicyLoadError(f"knowledge document has no valid id: {knowledge_file}")
        identifiers.add(metadata["id"])
        raw_policy_ids = metadata.get("policy_ids")
        if isinstance(raw_policy_ids, list):
            policy_ids.update(policy_id for policy_id in raw_policy_ids if isinstance(policy_id, str))
    return identifiers, policy_ids
