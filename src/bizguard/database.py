"""Transactional, checksum-protected PostgreSQL schema migrations."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re


_MIGRATION_NAME = re.compile(r"^[0-9]{3}_[a-z0-9_]+\.sql$")
_MIGRATION_LOCK = "bizguard-schema-migrations-v1"


def migration_files(directory: Path | None = None) -> list[Path]:
    """Return strictly named migrations in deterministic version order."""
    root = directory or Path(__file__).with_name("migrations")
    files = sorted(path for path in root.iterdir() if path.suffix == ".sql")
    if not files or any(_MIGRATION_NAME.fullmatch(path.name) is None for path in files):
        raise ValueError("database migrations are missing or have invalid names")
    versions = [path.name.split("_", 1)[0] for path in files]
    if len(versions) != len(set(versions)):
        raise ValueError("database migration versions must be unique")
    return files


def migrate(database_url: str, directory: Path | None = None) -> list[str]:
    """Apply pending migrations under one transaction-scoped advisory lock."""
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise ValueError("database URL must use PostgreSQL")
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - production extra supplies psycopg
        raise RuntimeError("database migrations require the production dependency extra") from exc

    migrations = migration_files(directory)
    applied: list[str] = []
    try:
        with psycopg.connect(database_url, autocommit=True) as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (_MIGRATION_LOCK,),
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS bizguard_schema_migrations ("
                    " version TEXT PRIMARY KEY,"
                    " checksum TEXT NOT NULL,"
                    " applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP"
                    ")"
                )
                existing = {
                    str(row[0]): str(row[1])
                    for row in connection.execute(
                        "SELECT version, checksum FROM bizguard_schema_migrations"
                    ).fetchall()
                }
                for path in migrations:
                    sql = path.read_text(encoding="utf-8")
                    checksum = sha256(sql.encode("utf-8")).hexdigest()
                    previous = existing.get(path.name)
                    if previous is not None:
                        if previous != checksum:
                            raise ValueError(
                                f"applied database migration checksum changed: {path.name}"
                            )
                        continue
                    connection.execute(sql)
                    connection.execute(
                        "INSERT INTO bizguard_schema_migrations (version, checksum) "
                        "VALUES (%s, %s)",
                        (path.name, checksum),
                    )
                    applied.append(path.name)
    except psycopg.Error as exc:
        sqlstate = f", sqlstate={exc.sqlstate}" if exc.sqlstate else ""
        raise RuntimeError(
            f"database migration failed ({type(exc).__name__}{sqlstate})"
        ) from exc
    return applied


def _database_url(arguments: argparse.Namespace) -> str:
    direct = arguments.database_url or os.environ.get("BIZGUARD_DATABASE_URL")
    file_name = arguments.database_url_file or os.environ.get("BIZGUARD_DATABASE_URL_FILE")
    if direct and file_name:
        raise ValueError("database URL and database URL file are mutually exclusive")
    if file_name:
        value = Path(file_name).read_text(encoding="utf-8").strip()
    else:
        value = direct or ""
    if not value:
        raise ValueError("database URL is required")
    return value


def main(argv: list[str] | None = None) -> int:
    """Apply database migrations without printing credentials."""
    parser = argparse.ArgumentParser(description="Apply BizGuard PostgreSQL migrations")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--database-url")
    group.add_argument("--database-url-file", type=Path)
    arguments = parser.parse_args(argv)
    try:
        applied = migrate(_database_url(arguments))
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "applied": applied}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
