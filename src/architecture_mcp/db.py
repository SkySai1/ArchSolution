"""SQLite infrastructure layer: connection, PRAGMAs, schema, migrations, transactions.

This is the ONLY place that opens a connection to the SQLite file. Domain
modules call helpers on `Database` and never touch `sqlite3` directly.

Contract:
- `db.connection` exposes the underlying `sqlite3.Connection` (row factory
  is `sqlite3.Row` for dict-like access).
- `db.initialize_schema()` applies pending versioned migrations from
  `migrations/NNN_name.sql`, tracking them in `schema_migrations`.
- `db.transaction()` is a context manager: BEGIN to yield to COMMIT on
  success, ROLLBACK on exception (the original `sqlite3` exception type
  is preserved so callers can match on it, e.g. `sqlite3.IntegrityError`).
- PRAGMAs are applied once at construction time.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: Environment variable that holds the absolute path to the SQLite file.
ENV_DB_PATH = "ARCH_MCP_DB"

#: Directory (relative to this file) holding SQL migrations.
_MIGRATIONS_DIRNAME = "migrations"
#: Migration file name pattern: `NNN_name.sql`.
_MIGRATION_GLOB = "[0-9][0-9][0-9]_*.sql"


def _strip_line_comments(sql: str) -> str:
    """Drop ``--`` line comments outside single-quoted string literals.

    The repo's migration scripts are simple DDL; the only thing to be careful
    about is a literal ``--`` inside a single-quoted string.
    """
    out_lines: list[str] = []
    in_string = False
    for raw in sql.splitlines():
        buf: list[str] = []
        i = 0
        n = len(raw)
        while i < n:
            ch = raw[i]
            if in_string:
                buf.append(ch)
                if ch == "'":
                    if i + 1 < n and raw[i + 1] == "'":
                        buf.append("'")
                        i += 1
                    else:
                        in_string = False
                i += 1
                continue
            if ch == "'":
                in_string = True
                buf.append(ch)
                i += 1
                continue
            if ch == "-" and i + 1 < n and raw[i + 1] == "-":
                break  # comment: rest of the line is ignored
            buf.append(ch)
            i += 1
        out_lines.append("".join(buf))
    return "\n".join(out_lines)


def _split_statements(sql: str) -> list[str]:
    """Split SQL into top-level statements on ``;`` (string-literal aware)."""
    cleaned = _strip_line_comments(sql)
    stmts: list[str] = []
    buf: list[str] = []
    in_string = False
    i = 0
    n = len(cleaned)
    while i < n:
        ch = cleaned[i]
        if in_string:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and cleaned[i + 1] == "'":
                    buf.append("'")
                    i += 1
                else:
                    in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


class Database:
    """A thin, synchronous wrapper over `sqlite3` tuned for MCP workloads."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit mode (isolation_level=None) — we drive transactions
        # explicitly via `conn` below, which gives us deterministic rollback
        # across DDL / DML / PRAGMA in one connection.
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                    isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        # Unicode case-folding for case-insensitive text search (SQL LIKE only
        # folds ASCII, so searches over Cyrillic text need an explicit UDF).
        self._conn.create_function(
            "ilower", 1,
            lambda s: s.lower() if isinstance(s, str) else s,
            deterministic=True,
        )
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

    def migrations_dir(self) -> Path:
        """Absolute path to the migrations directory next to this file."""
        return Path(__file__).parent / _MIGRATIONS_DIRNAME

    # -- transactions -------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run statements inside an explicit transaction.

        With `isolation_level=None` (autocommit by default), the caller's
        statements are executed in autocommit mode; we therefore open an
        explicit ``BEGIN IMMEDIATE`` on entry and commit/rollback on exit.
        Any exception (including `sqlite3.IntegrityError`) is re-raised
        after ROLLBACK so callers can match on the exception type.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # -- schema -------------------------------------------------------------------

    def initialize_schema(self) -> None:
        """Apply pending schema migrations in version order, then commit.

        Migrations live in ``migrations/NNN_name.sql`` and are applied once;
        applied versions are tracked in ``schema_migrations``. Each migration
        is executed statement-by-statement inside an explicit transaction so a
        failure raises and the schema is left untouched.

        ``sqlite3.executescript`` is deliberately NOT used: it issues an
        implicit COMMIT before running the script, which would break rollback
        semantics on a failing migration.
        """
        mig_dir = self.migrations_dir()
        if not mig_dir.is_dir():
            raise RuntimeError(f"migrations directory not found: {mig_dir}")

        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "    version    INTEGER PRIMARY KEY,"
            "    name       TEXT UNIQUE NOT NULL,"
            "    applied_at TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

        applied = {
            r[0] for r in self._conn.execute("SELECT version FROM schema_migrations")
        }
        for mig_file in sorted(mig_dir.glob(_MIGRATION_GLOB)):
            version = int(mig_file.name[:3])
            if version in applied:
                continue
            statements = _split_statements(mig_file.read_text(encoding="utf-8"))
            with self.transaction() as conn:
                for sql in statements:
                    conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations "
                    "(version, name, applied_at) VALUES (?, ?, datetime('now'))",
                    (version, mig_file.stem),
                )
