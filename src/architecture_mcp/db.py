"""SQLite infrastructure layer: connection, PRAGMAs, schema, transactions.

This is the ONLY place that opens a connection to the SQLite file. Domain
modules call helpers on `Database` and never touch `sqlite3` directly.

Contract:
- `db.connection` exposes the underlying `sqlite3.Connection` (row factory
  is `sqlite3.Row` for dict-like access).
- `db.initialize_schema()` applies the SQL DDL idempotently.
- `db.transaction()` is a context manager: BEGIN → yield → COMMIT on success,
  ROLLBACK on exception (the original `sqlite3` exception is re-raised).
- PRAGMAs are applied once at construction time.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: Environment variable that holds the absolute path to the SQLite file.
ENV_DB_PATH = "ARCH_MCP_DB"


class Database:
    """A thin, synchronous wrapper over `sqlite3` tuned for MCP workloads."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._apply_pragmas()

    # -- connection --------------------------------------------------------------

    def _apply_pragmas(self) -> None:
        c = self._conn
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA synchronous = NORMAL")
        c.execute("PRAGMA busy_timeout = 5000")
        c.commit()

    def close(self) -> None:
        self._conn.close()

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the raw connection for SQL execution inside domain modules."""
        return self._conn

    # -- transactions -------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run statements under an explicit transaction.

        Commits on success, rolls back and re-raises on any exception
        (the original `sqlite3` exception type is preserved so callers
        can match on it, e.g. `sqlite3.IntegrityError`).
        """
        try:
            yield self._conn
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    # -- schema -------------------------------------------------------------------

    def initialize_schema(self) -> None:
        """Create all tables + indexes if they do not already exist (idempotent)."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id          INTEGER PRIMARY KEY,
                source_type TEXT    NOT NULL,
                title       TEXT    NOT NULL,
                version     TEXT,
                file_path   TEXT,
                status      TEXT    NOT NULL DEFAULT 'ACTIVE',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS npa (
                source_id     INTEGER PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                document_type TEXT NOT NULL,
                number        TEXT,
                issuer        TEXT,
                status        TEXT NOT NULL DEFAULT 'ACTIVE'
            );
            CREATE INDEX IF NOT EXISTS idx_npa_status ON npa(status);

            CREATE TABLE IF NOT EXISTS architectures (
                id          INTEGER PRIMARY KEY,
                name        TEXT    NOT NULL,
                version     TEXT,
                description TEXT,
                status      TEXT    NOT NULL DEFAULT 'ACTIVE',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_architectures_name
                ON architectures(name, coalesce(version, ''));

            CREATE TABLE IF NOT EXISTS requirements (
                id              INTEGER PRIMARY KEY,
                source_id       INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                source_locator  TEXT,
                source_quote    TEXT,
                requirement_text TEXT   NOT NULL,
                normalized_text TEXT,
                status          TEXT    NOT NULL DEFAULT 'ACTIVE',
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_requirements_source ON requirements(source_id);
            CREATE INDEX IF NOT EXISTS idx_requirements_status ON requirements(status);

            CREATE TABLE IF NOT EXISTS facts (
                id              INTEGER PRIMARY KEY,
                architecture_id INTEGER NOT NULL REFERENCES architectures(id) ON DELETE CASCADE,
                source_id       INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                source_locator  TEXT,
                source_quote    TEXT,
                fact_text       TEXT    NOT NULL,
                normalized_text TEXT,
                status          TEXT    NOT NULL DEFAULT 'ACTIVE',
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_facts_arch ON facts(architecture_id);
            CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source_id);

            CREATE TABLE IF NOT EXISTS categories (
                id          INTEGER PRIMARY KEY,
                parent_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                name        TEXT    NOT NULL,
                description TEXT,
                scope       TEXT    NOT NULL DEFAULT 'BOTH',
                status      TEXT    NOT NULL DEFAULT 'ACTIVE',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_categories_name
                ON categories(name, coalesce(parent_id, 0));

            CREATE TABLE IF NOT EXISTS requirement_categories (
                requirement_id INTEGER NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
                category_id    INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                confidence     REAL    NOT NULL DEFAULT 1.0,
                PRIMARY KEY (requirement_id, category_id)
            );
            CREATE INDEX IF NOT EXISTS idx_rcat_category
                ON requirement_categories(category_id);

            CREATE TABLE IF NOT EXISTS fact_categories (
                fact_id    INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
                category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                confidence REAL    NOT NULL DEFAULT 1.0,
                PRIMARY KEY (fact_id, category_id)
            );
            CREATE INDEX IF NOT EXISTS idx_fcat_category
                ON fact_categories(category_id);

            CREATE TABLE IF NOT EXISTS assessments (
                id               INTEGER PRIMARY KEY,
                requirement_id   INTEGER NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
                architecture_id  INTEGER NOT NULL REFERENCES architectures(id) ON DELETE CASCADE,
                result           TEXT    NOT NULL,
                rationale        TEXT    NOT NULL,
                confidence       REAL    NOT NULL DEFAULT 1.0,
                created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_assessments_pair
                ON assessments(requirement_id, architecture_id);
            CREATE INDEX IF NOT EXISTS idx_assessments_arch
                ON assessments(architecture_id);

            CREATE TABLE IF NOT EXISTS assessment_facts (
                assessment_id INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
                fact_id       INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
                relation_type TEXT    NOT NULL CHECK (relation_type
                    IN ('SUPPORTS','CONTRADICTS','CONTEXT')),
                PRIMARY KEY (assessment_id, fact_id)
            );
            CREATE INDEX IF NOT EXISTS idx_afact_fact
                ON assessment_facts(fact_id);
            """
        )
        self._conn.commit()
