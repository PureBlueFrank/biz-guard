"""OpenAPI YAML and Proto token parsers retaining parser-derived positions."""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
import yaml  # type: ignore[import-untyped]
from bizguard.graph.ids import api_id, proto_id


@dataclass(frozen=True)
class ContractFact:
    """Represent one extracted API contract fact."""

    kind: str
    name: str
    node_id: str
    line: int
    column: int
    evidence_uri: str


def analyze_openapi(path: Path, service: str, revision: str) -> list[ContractFact]:
    """Extract endpoint and schema-field facts from an OpenAPI document."""
    root = yaml.compose(path.read_text(encoding="utf-8"))
    facts: list[ContractFact] = []
    relative = str(path).split(f"{service}/", 1)[-1]
    if root is None:
        return facts

    def walk(node: object, keys: list[str]) -> None:
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                value_key = str(key.value)
                nxt = keys + [value_key]
                if (
                    len(keys) >= 2
                    and keys[-1].startswith("/")
                    and value_key.lower() in {"get", "post", "put", "delete", "patch"}
                ):
                    facts.append(
                        ContractFact(
                            "endpoint",
                            f"{value_key.upper()} {keys[-1]}",
                            api_id(service, value_key, keys[-1]),
                            key.start_mark.line + 1,
                            key.start_mark.column + 1,
                            f"repo://{service}/{relative}#L{key.start_mark.line + 1}:C{key.start_mark.column + 1}",
                        )
                    )
                if keys and keys[-1] == "properties":
                    facts.append(
                        ContractFact(
                            "schema_field",
                            value_key,
                            api_id(service, "SCHEMA", value_key),
                            key.start_mark.line + 1,
                            key.start_mark.column + 1,
                            f"repo://{service}/{relative}#L{key.start_mark.line + 1}:C{key.start_mark.column + 1}",
                        )
                    )
                walk(value, nxt)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                walk(item, keys)

    walk(root, [])
    return facts


def analyze_proto(path: Path, revision: str, repository: str = "coupon-contract") -> list[ContractFact]:
    """Extract scalar field facts from a Protocol Buffers document."""
    facts: list[ContractFact] = []
    package = "default"
    message = ""
    relative = str(path).split(f"{repository}/", 1)[-1]
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if match := re.match(r"\s*package\s+([\w.]+)", line):
            package = match.group(1)
        if match := re.search(r"\bmessage\s+(\w+)", line):
            message = match.group(1)
        for match in re.finditer(r"\b(?:string|int32|int64|bool)\s+(\w+)\s*=", line):
            name = match.group(1)
            col = match.start(1) + 1
            facts.append(
                ContractFact(
                    "proto_field",
                    name,
                    proto_id(package, message, name),
                    line_no,
                    col,
                    f"repo://{repository}/{relative}#L{line_no}:C{col}",
                )
            )
    return facts
