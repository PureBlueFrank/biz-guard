"""tree-sitter Java extraction with byte/line precise source evidence."""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
from tree_sitter import Language, Parser
import tree_sitter_java as tsjava
from bizguard.graph.ids import java_symbol, repo_id

_PARSER = Parser(Language(tsjava.language()))


@dataclass(frozen=True)
class JavaFact:
    kind: str
    name: str
    node_id: str
    line: int
    column: int
    evidence_uri: str
    owner: str | None = None
    container: str | None = None
    receiver: str | None = None
    type_name: str | None = None
    annotations: tuple[str, ...] = ()


def analyze(path: Path, repository: str, revision: str) -> list[JavaFact]:
    raw = path.read_bytes()
    tree = _PARSER.parse(raw)
    relative = str(path).split(f"{repository}/", 1)[-1]
    facts: list[JavaFact] = []

    def text(node: Any) -> str:
        return raw[node.start_byte : node.end_byte].decode("utf-8")

    def annotations(node: Any) -> tuple[str, ...]:
        modifiers = node.child_by_field_name("modifiers") or next(
            (child for child in node.children if child.type == "modifiers"), None
        )
        if modifiers is None:
            return ()
        return tuple(re.findall(r"@(\w+)", text(modifiers)))

    def declared_type(node: Any) -> str | None:
        type_node = node.child_by_field_name("type")
        return text(type_node).split("<", 1)[0].strip() if type_node is not None else None

    def visit(node: Any, owner: str | None = None, method: str | None = None) -> None:
        typ = node.type
        name_node = node.child_by_field_name("name")
        name = text(name_node) if name_node else ""
        point = node.start_point
        uri = f"repo://{repository}/{relative}#L{point.row + 1}:C{point.column + 1}"
        current_owner = owner
        current_method = method
        if typ in {"class_declaration", "interface_declaration", "record_declaration"} and name:
            current_owner = name
            facts.append(
                JavaFact(
                    "class",
                    name,
                    repo_id(repository, relative, name),
                    point.row + 1,
                    point.column + 1,
                    uri,
                    annotations=annotations(node),
                )
            )
        elif typ in {"field_declaration", "formal_parameter"}:
            declarator = node.child_by_field_name("declarator")
            field_name = (
                text(declarator.child_by_field_name("name"))
                if declarator and declarator.child_by_field_name("name")
                else name
            )
            if field_name and current_owner:
                facts.append(
                    JavaFact(
                        "parameter" if typ == "formal_parameter" else "field",
                        field_name,
                        repo_id(repository, relative, f"{current_owner}.{field_name}"),
                        point.row + 1,
                        point.column + 1,
                        uri,
                        owner=current_owner,
                        type_name=declared_type(node),
                    )
                )
        elif typ in {"method_declaration", "constructor_declaration"} and name and current_owner:
            facts.append(
                JavaFact(
                    "method",
                    name,
                    repo_id(repository, relative, java_symbol(current_owner, name)),
                    point.row + 1,
                    point.column + 1,
                    uri,
                    owner=current_owner,
                    annotations=annotations(node),
                )
            )
            current_method = name
        elif typ == "method_invocation" and name:
            object_node = node.child_by_field_name("object")
            facts.append(
                JavaFact(
                    "call",
                    name,
                    repo_id(repository, relative, f"call:{name}@{point.row + 1}"),
                    point.row + 1,
                    point.column + 1,
                    uri,
                    owner=current_owner,
                    container=current_method,
                    receiver=text(object_node) if object_node is not None else None,
                )
            )
        for child in node.children:
            visit(child, current_owner, current_method)

    visit(tree.root_node)
    return facts
