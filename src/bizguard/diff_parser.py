"""Validated parsing for the bounded unified-diff input format."""

from typing import Literal

from pydantic import BaseModel


class DiffParseError(ValueError):
    """Raised when an input is not a supported unified diff."""


class ParsedHunk(BaseModel):
    """One parsed unified-diff hunk."""

    header: str
    lines: list[str]


class ParsedFile(BaseModel):
    """A changed Python file represented by a unified diff."""

    old_path: str | None
    new_path: str | None
    operation: Literal["add", "delete", "modify", "rename"]
    hunks: list[ParsedHunk]


class ParsedDiff(BaseModel):
    """The collection of changed files accepted by BizGuard."""

    files: list[ParsedFile]


_ALLOWED_PREFIXES = ("sample/coupon-service/", "sample/merchant-gateway/")


def parse(diff_text: str) -> ParsedDiff:
    """Parse the supported, source-only subset of a unified diff.

    Each file must have git and unified headers plus at least one hunk.  The
    narrow format is intentional: decisions are only meaningful for the two
    sample Python services covered by the version-one contract registry.
    """
    lines = diff_text.splitlines()
    if not lines or any(line.startswith("GIT binary patch") for line in lines):
        raise DiffParseError("input must be a non-binary unified diff")

    files: list[ParsedFile] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("diff --git "):
            raise DiffParseError("each changed file must begin with a diff --git header")
        header_parts = lines[index].split()
        if len(header_parts) != 4:
            raise DiffParseError("invalid diff --git header")
        git_old, git_new = header_parts[2], header_parts[3]
        if not git_old.startswith("a/") or not git_new.startswith("b/"):
            raise DiffParseError("diff --git paths must use a/ and b/ prefixes")
        index += 1
        rename_from: str | None = None
        rename_to: str | None = None
        old_path: str | None = None
        new_path: str | None = None
        has_old_header = False
        has_new_header = False
        hunks: list[ParsedHunk] = []
        while index < len(lines) and not lines[index].startswith("diff --git "):
            line = lines[index]
            if line.startswith("Binary files "):
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
                hunk_lines = [line]
                index += 1
                while index < len(lines) and not lines[index].startswith(("diff --git ", "@@ ")):
                    hunk_lines.append(lines[index])
                    index += 1
                hunks.append(ParsedHunk(header=hunk_lines[0], lines=hunk_lines[1:]))
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
        files.append(
            ParsedFile(old_path=old_path, new_path=new_path, operation=operation, hunks=hunks)
        )
    return ParsedDiff(files=files)


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
    if not path.startswith(_ALLOWED_PREFIXES) or not path.endswith(".py"):
        raise DiffParseError(f"unsupported source path: {path}")
