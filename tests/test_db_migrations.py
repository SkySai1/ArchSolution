"""Migration lifecycle tests (ADR-001 §8/§9 items 8-10)."""
from __future__ import annotations

import pathlib
import sqlite3

import pytest

from architecture_mcp.db import Database


def _schema_version(db: Database) -> int | None:
    row = db.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return row[0] if row else None


def test_migrations_table_tracks_applied_versions(db: Database) -> None:
    rows = db.connection.execute(
        "SELECT version, name FROM schema_migrations ORDER BY version"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [(1, "001_initial")]


def test_already_applied_migration_not_rerun(db: Database) -> None:
    before = db.connection.execute(
        "SELECT COUNT(*) FROM schema_migrations"
    ).fetchone()[0]
    db.initialize_schema()  # second run
    after = db.connection.execute(
        "SELECT COUNT(*) FROM schema_migrations"
    ).fetchone()[0]
    assert before == after == 1


def test_existing_old_db_is_upgraded_without_data_loss(
    db_path: pathlib.Path,
) -> None:
    """Simulate an old database (schema without schema_migrations) with
    existing rows; opening it via a fresh Database must migrate and keep data."""
    # Build a "legacy" DB: same schema as 001 minus the tracker table.
    db_path.unlink(missing_ok=True)
    legacy = sqlite3.connect(str(db_path))
    legacy.execute(
        "CREATE TABLE sources ("
        " id INTEGER PRIMARY KEY, source_type TEXT NOT NULL, "
        " title TEXT NOT NULL, version TEXT, file_path TEXT, "
        " status TEXT NOT NULL DEFAULT 'ACTIVE', "
        " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    cur = legacy.execute(
        "INSERT INTO sources (source_type, title) VALUES ('NPA', 'legacy doc')"
    )
    legacy.commit()
    legacy.close()
    data_id = cur.lastrowid

    # New Database instance migrates the existing file.
    db = Database(db_path)
    try:
        db.initialize_schema()
        row = db.connection.execute(
            "SELECT id, title FROM sources WHERE id = ?", (data_id,)
        ).fetchone()
        assert row is not None and row["title"] == "legacy doc"
        assert _schema_version(db) == 1
    finally:
        db.close()


def test_failed_migration_leaves_no_partial_state(db_path: pathlib.Path) -> None:
    """A broken second migration must not be half-applied and must not
    be recorded in schema_migrations."""

    # Apply the shipped 001 first, then drop a broken 002.
    db = Database(db_path)
    db.initialize_schema()
    db.close()

    mig_dir = pathlib.Path("src/architecture_mcp/migrations")
    broken = mig_dir / "002_broken.sql"
    broken.write_text("CREATE TABLE broken_step (id INTEGER); SELECT * FROM missing_table;\n")
    try:
        db2 = Database(db_path)
        try:
            with pytest.raises(sqlite3.OperationalError):
                db2.initialize_schema()
        finally:
            db2.close()
    finally:
        broken.unlink(missing_ok=True)

    # The failed migration must not be recorded, and no partial table.
    check = sqlite3.connect(str(db_path))
    recorded = check.execute(
        "SELECT version FROM schema_migrations WHERE version = 2"
    ).fetchone()
    assert recorded is None
    tables = {
        r[0]
        for r in check.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "broken_step" not in tables
    check.close()
