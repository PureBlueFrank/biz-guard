"""Database migrations are deterministic and never expose connection secrets."""

from pathlib import Path

import pytest

from bizguard.database import main, migration_files


def test_bundled_database_migrations_have_unique_ordered_versions() -> None:
    files = migration_files()
    assert [path.name for path in files] == ["001_initial.sql"]
    assert all(path.read_text(encoding="utf-8").strip() for path in files)


def test_database_migration_cli_rejects_non_postgres_url_without_echoing_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "not-postgres://operator:do-not-print@example.test/bizguard"
    assert main(["--database-url", secret]) == 1
    captured = capsys.readouterr()
    assert secret not in captured.out


def test_custom_migration_names_must_be_versioned(tmp_path: Path) -> None:
    (tmp_path / "migration.sql").write_text("SELECT 1;", encoding="utf-8")
    try:
        migration_files(tmp_path)
    except ValueError as error:
        assert "invalid names" in str(error)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("invalid migration name was accepted")
