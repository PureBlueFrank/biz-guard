"""Shared, fail-closed reconstruction of post-change file content."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bizguard.diff_parser import ParsedFile


class ReconstructionError(ValueError):
    """Raised when a diff cannot be safely applied to its repository baseline."""


@dataclass(frozen=True)
class ReconstructedFile:
    """Hold the trusted baseline and reconstructed post-change text."""

    before: str
    after: str


def reconstruct_file(file: ParsedFile, repository_root: Path) -> ReconstructedFile:
    """Reconstruct both sides when disk contains either the before or after state."""
    if file.operation == "add":
        patched = apply_hunks([], file)
        if patched is None:
            raise ReconstructionError("new-file hunks do not apply to an empty baseline")
        generated = _lines_to_text(patched, _diff_has_trailing_newline(file))
        target = _base_path(file, repository_root)
        if target.is_file():
            disk = target.read_text(encoding="utf-8")
            if disk.splitlines() == patched:
                generated = disk
        return ReconstructedFile(before="", after=generated)

    base_path = _base_path(file, repository_root)
    if not base_path.is_file():
        if file.operation == "delete":
            before = reverse_hunks([], file)
            if before is not None:
                return ReconstructedFile(
                    before=_lines_to_text(before, _diff_has_trailing_newline(file)),
                    after="",
                )
        path = file.old_path or file.new_path or "<unknown>"
        raise ReconstructionError(f"repository file exists in neither diff state: {path}")
    disk = base_path.read_text(encoding="utf-8")
    if file.operation == "delete":
        patched = apply_hunks(disk.splitlines(), file)
        if patched is None or patched:
            raise ReconstructionError("delete hunks do not remove the exact repository baseline")
        return ReconstructedFile(before=disk, after="")
    if not file.hunks:
        return ReconstructedFile(before=disk, after=disk)
    patched = apply_hunks(disk.splitlines(), file)
    reconstructed_before = reverse_hunks(disk.splitlines(), file)
    if patched is not None and reconstructed_before is not None:
        line_delta = sum(
            len(hunk.added_lines) - len(hunk.removed_lines) for hunk in file.hunks
        )
        if line_delta > 0:
            patched = None
        else:
            reconstructed_before = None
    if patched is not None:
        return ReconstructedFile(
            before=disk,
            after=_lines_to_text(patched, disk.endswith("\n")),
        )
    if reconstructed_before is not None:
        return ReconstructedFile(
            before=_lines_to_text(reconstructed_before, disk.endswith("\n")),
            after=disk,
        )
    raise ReconstructionError("hunk context matches neither repository diff state")


def apply_hunks(base: list[str], file: ParsedFile) -> list[str] | None:
    """Apply hunks by their validated line positions, returning None on mismatch."""
    return _apply_hunks(base, file, reverse=False)


def reverse_hunks(after: list[str], file: ParsedFile) -> list[str] | None:
    """Reverse validated hunks when the working tree already contains the change."""
    return _apply_hunks(after, file, reverse=True)


def _apply_hunks(
    base: list[str], file: ParsedFile, *, reverse: bool
) -> list[str] | None:
    if not file.hunks:
        return list(base)
    output: list[str] = []
    cursor = 0
    for hunk in file.hunks:
        start_line = hunk.new_start if reverse else hunk.old_start
        preferred = start_line - 1 if start_line else 0
        removed_marker = "+" if reverse else "-"
        added_marker = "-" if reverse else "+"
        expected = [
            line[1:]
            for line in hunk.lines
            if line.startswith((" ", removed_marker))
        ]
        start = _locate_hunk(base, expected, preferred, cursor)
        if start is None:
            return None
        if start < cursor or start > len(base):
            return None
        output.extend(base[cursor:start])
        cursor = start
        for line in hunk.lines:
            if line.startswith("\\"):
                continue
            marker, value = line[:1], line[1:]
            if marker in {" ", removed_marker}:
                if cursor >= len(base) or base[cursor] != value:
                    return None
                if marker == " ":
                    output.append(value)
                cursor += 1
            elif marker == added_marker:
                output.append(value)
            else:
                return None
    output.extend(base[cursor:])
    return output


def _locate_hunk(
    lines: list[str], expected: list[str], preferred: int, minimum: int
) -> int | None:
    """Locate a hunk in linear time, accepting a fallback only when it is unique."""
    if preferred >= minimum and lines[preferred : preferred + len(expected)] == expected:
        return preferred
    if not expected:
        return preferred if minimum <= preferred <= len(lines) else None
    prefix = _prefix_table(expected)
    matches: list[int] = []
    matched = 0
    for position in range(minimum, len(lines)):
        while matched and lines[position] != expected[matched]:
            matched = prefix[matched - 1]
        if lines[position] == expected[matched]:
            matched += 1
            if matched == len(expected):
                matches.append(position - len(expected) + 1)
                if len(matches) > 1:
                    return None
                matched = prefix[matched - 1]
    return matches[0] if matches else None


def _prefix_table(pattern: list[str]) -> list[int]:
    table = [0] * len(pattern)
    matched = 0
    for position in range(1, len(pattern)):
        while matched and pattern[position] != pattern[matched]:
            matched = table[matched - 1]
        if pattern[position] == pattern[matched]:
            matched += 1
            table[position] = matched
    return table


def _base_path(file: ParsedFile, repository_root: Path) -> Path:
    candidates: list[Path] = []
    for raw_path in (file.old_path, file.new_path):
        if raw_path is None:
            continue
        relative = Path(raw_path)
        candidates.append(repository_root / relative)
        if repository_root.name in relative.parts:
            marker = relative.parts.index(repository_root.name)
            candidates.append(repository_root.joinpath(*relative.parts[marker + 1 :]))
        repository_names = {
            child.name for child in repository_root.iterdir() if child.is_dir()
        }
        for marker, part in enumerate(relative.parts):
            if part in repository_names:
                candidates.append(repository_root.joinpath(*relative.parts[marker:]))
    if not candidates:
        candidates.append(repository_root)
    return next((path for path in candidates if path.is_file()), candidates[0])


def _diff_has_trailing_newline(file: ParsedFile) -> bool:
    return not any(line.startswith("\\ No newline") for hunk in file.hunks for line in hunk.lines)


def _lines_to_text(lines: list[str], trailing_newline: bool) -> str:
    text = "\n".join(lines)
    return text + "\n" if lines and trailing_newline else text
