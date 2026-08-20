"""Frozen canonical identifiers for repository and integration graph nodes.

Identifiers deliberately use repository names, never local absolute paths.  Path
segments use POSIX separators and symbols are carried in URI fragments.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote


def repo_id(repository: str, path: str, symbol: str | None = None) -> str:
    """Return ``repo://<repo>/<path>[#<symbol>]`` for a source symbol."""
    result = f"repo://{_part(repository)}/{_path(path)}"
    return f"{result}#{_fragment(symbol)}" if symbol else result


def api_id(service: str, method: str, path: str, operation: str | None = None) -> str:
    """Return ``api://<service>/<METHOD><path>[#<operation>]``."""
    normalized_path = "/" + _path(path).lstrip("/")
    result = f"api://{_part(service)}/{_part(method.upper())}{normalized_path}"
    return f"{result}#{_fragment(operation)}" if operation else result


def proto_id(package: str, service: str, method: str) -> str:
    """Return ``proto://<package>/<service>/<method>`` for an RPC method."""
    return f"proto://{_part(package)}/{_part(service)}/{_part(method)}"


def db_id(repository: str, table: str, column: str | None = None) -> str:
    """Return ``db://<table-owner>/<table>[#<column>]`` for persistent data.

    ``repository`` is always the repository that owns the physical table.
    """
    result = f"db://{_part(repository)}/{_part(table)}"
    return f"{result}#{_fragment(column)}" if column else result


def mq_id(service: str, topic: str, message: str | None = None) -> str:
    """Return ``mq://<producer-owner>/<topic>[#<message>]`` for a message channel.

    ``service`` is always the owning producer, even when a consumer is indexed.
    """
    result = f"mq://{_part(service)}/{_part(topic)}"
    return f"{result}#{_fragment(message)}" if message else result


def java_symbol(class_name: str, method: str, parameter_types: tuple[str, ...] = ()) -> str:
    """Return the frozen Java method fragment ``Class.method(Type,Type)``."""
    return (
        f"{_part(class_name)}.{_part(method)}({','.join(_part(item) for item in parameter_types)})"
    )


def _part(value: str) -> str:
    if not value or value.strip() != value:
        raise ValueError("identifier components must be non-empty and trimmed")
    return quote(value, safe="._-~")


def _path(value: str) -> str:
    normalized = str(PurePosixPath(value.replace("\\", "/"))).lstrip("/")
    if not normalized or normalized == "." or normalized.startswith("../"):
        raise ValueError("path must be a non-empty relative path")
    return "/".join(_part(part) for part in normalized.split("/"))


def _fragment(value: str) -> str:
    return quote(value, safe="._-~()[],")
