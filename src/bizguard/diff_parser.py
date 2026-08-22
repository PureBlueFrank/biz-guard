"""Validated parsing for the multi-file unified-diff input format."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field


class DiffParseError(ValueError):
    """Raised when an input is not a supported unified diff."""


class ParsedHunk(BaseModel):
    """One parsed unified-diff hunk with its added and removed lines."""

    header: str
    lines: list[str]
    added_lines: list[str] = Field(default_factory=list)
    removed_lines: list[str] = Field(default_factory=list)


class ParsedFile(BaseModel):
    """A changed file represented by a unified diff."""

    old_path: str | None
    new_path: str | None
    operation: Literal["add", "delete", "modify", "rename"]
    hunks: list[ParsedHunk]
    added_lines: list[str] = Field(default_factory=list)
    removed_lines: list[str] = Field(default_factory=list)


class ParsedDiff(BaseModel):
    """The collection of changed files accepted by BizGuard."""

    files: list[ParsedFile]


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_LEGACY_ALLOWED_PREFIXES = ("sample/coupon-service/", "sample/merchant-gateway/")


def parse_unified(diff_text: str) -> ParsedDiff:
    """Parse a multi-file unified diff into per-file change records.

    Each file must carry ``diff --git`` and ``---``/``+++`` headers plus at
    least one valid hunk.  Binary diffs, truncated hunks, missing paths and
    paths that escape the repository root raise :class:`DiffParseError` so
    callers surface a fault instead of treating the input as a no-risk change.
    """
    lines = diff_text.splitlines()
    if not lines:
        raise DiffParseError("input must be a non-empty unified diff")

    files: list[ParsedFile] = []
    index = 0
    while index < len(lines):
        while index < len(lines) and not lines[index].startswith("diff --git "):
            index += 1
        if index >= len(lines):
            break
        parsed_file, index = _parse_file(lines, index)
        files.append(parsed_file)

    if not files:
        raise DiffParseError("input contains no diff --git file header")
    return ParsedDiff(files=files)


def _parse_file(lines: list[str], start: int) -> tuple[ParsedFile, int]:
    header_parts = lines[start].split()
    if len(header_parts) != 4:
        raise DiffParseError("invalid diff --git header")
    git_old, git_new = header_parts[2], header_parts[3]
    if not git_old.startswith("a/") or not git_new.startswith("b/"):
        raise DiffParseError("diff --git paths must use a/ and b/ prefixes")

    index = start + 1
    rename_from: str | None = None
    rename_to: str | None = None
    old_path: str | None = None
    new_path: str | None = None
    has_old_header = False
    has_new_header = False
    hunks: list[ParsedHunk] = []

    while index < len(lines) and not lines[index].startswith("diff --git "):
        line = lines[index]
        if line.startswith("GIT binary patch") or line.startswith("Binary files "):
            raise DiffParseError("binary diffs are not supported")
        if line.startswith("rename from "):
            rename_from = line.removeprefix("rename from ")
        elif line.startswith("rename to "):
            rename_to = line.removeprefix("rename to ")
        elif line.startswith("--- "):
            old_path = _header_path(line.removeprefix("--- "), "a/")
            has_old_header = True
        elif line.startswith("+++ "):
            new_path = _header_path(line.removeprefix("+++ "), "b/")
            has_new_header = True
        elif line.startswith("@@ "):
            hunk, index = _parse_hunk(lines, index)
            hunks.append(hunk)
            continue
        index += 1

    if not has_old_header or not has_new_header or not hunks:
        raise DiffParseError("each changed file needs ---, +++, and at least one @@ hunk")

    operation: Literal["add", "delete", "modify", "rename"]
    if rename_from is not None or rename_to is not None:
        if rename_from is None or rename_to is None:
            raise DiffParseError("renames must provide both rename from and rename to")
        old_path, new_path, operation = rename_from, rename_to, "rename"
    elif old_path is None:
        operation = "add"
    elif new_path is None:
        operation = "delete"
    else:
        operation = "modify"

    for path in (old_path, new_path):
        if path is not None:
            _validate_path(path)

    added_lines = [line for hunk in hunks for line in hunk.added_lines]
    removed_lines = [line for hunk in hunks for line in hunk.removed_lines]

    return (
        ParsedFile(
            old_path=old_path,
            new_path=new_path,
            operation=operation,
            hunks=hunks,
            added_lines=added_lines,
            removed_lines=removed_lines,
        ),
        index,
    )


def _parse_hunk(lines: list[str], start: int) -> tuple[ParsedHunk, int]:
    header = lines[start]
    match = _HUNK_HEADER.match(header)
    if match is None:
        raise DiffParseError(f"malformed hunk header: {header}")
    expected_old = int(match.group(2) or 1)
    expected_new = int(match.group(4) or 1)

    index = start + 1
    body: list[str] = []
    while index < len(lines) and not lines[index].startswith(("diff --git ", "@@ ")):
        body.append(lines[index])
        index += 1

    added_lines: list[str] = []
    removed_lines: list[str] = []
    actual_old = 0
    actual_new = 0
    for line in body:
        if line.startswith("\\"):
            continue
        if line.startswith("+"):
            added_lines.append(line[1:])
            actual_new += 1
        elif line.startswith("-"):
            removed_lines.append(line[1:])
            actual_old += 1
        elif line.startswith(" "):
            actual_old += 1
            actual_new += 1
        else:
            raise DiffParseError(f"unsupported hunk line: {line!r}")

    if (actual_old, actual_new) != (expected_old, expected_new):
        raise DiffParseError(
            f"truncated hunk: header declares -{expected_old},+{expected_new} "
            f"but body has -{actual_old},+{actual_new}"
        )

    return ParsedHunk(header=header, lines=body, added_lines=added_lines, removed_lines=removed_lines), index


def _header_path(value: str, prefix: str) -> str | None:
    path = value.split("\t", maxsplit=1)[0]
    if path == "/dev/null":
        return None
    if not path.startswith(prefix):
        raise DiffParseError(f"path must use {prefix} prefix")
    return path.removeprefix(prefix)


def _validate_path(path: str) -> None:
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise DiffParseError(f"unsafe path: {path!r}")


def _validate_legacy_path(path: str) -> None:
    if not path.startswith(_LEGACY_ALLOWED_PREFIXES) or not path.endswith(".py"):
        raise DiffParseError(f"unsupported source path: {path}")


def parse(diff_text: str) -> ParsedDiff:
    """Legacy single-service adapter: parse the diff, then enforce the version-one allowlist.

    Kept for backward compatibility with the Python sample pipeline; new
    callers should use :func:`parse_unified` directly.
    """
    parsed = parse_unified(diff_text)
    for file in parsed.files:
        for path in (file.old_path, file.new_path):
            if path is not None:
                _validate_legacy_path(path)
    return parsed
