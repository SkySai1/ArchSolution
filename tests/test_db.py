"""Tests for architecture_mcp.db (schema, PRAGMAs, transactions)."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

import pytest

from architecture_mcp.db import Database

EXPECTED_TABLES = {
    "sources",
    "npa",
    "architectures",
    "requirements",
    "facts",
    "categories",
    "requirement_categories",
    "fact_categories",
    "assessments",
    "assessment_facts",
}


def expect_error(fn: Callable[[], None], exc: type[Exception] = sqlite3.Error) -> None:
    with pytest.raises(exc):
        fn()


def test_schema_creates_all_expected_tables(db: Database) -> None:
    rows = db.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    assert {r[0] for r in rows} >= EXPECTED_TABLES


def test_schema_is_idempotent(db: Database) -> None:
    db.initialize_schema()
    db.initialize_schema()  # second call must not raise
    count = db.connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()[0]
    assert count == 1


def test_pragmas_are_applied(db: Database) -> None:
    assert db.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    # NORMAL = 1
    assert db.connection.execute("PRAGMA synchronous").fetchone()[0] == 1


def test_foreign_keys_are_enforced(db: Database) -> None:
    def bad() -> None:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO requirements (source_id, requirement_text) VALUES (?, ?)",
                (9999, "should fail"),
            )

    expect_error(bad, sqlite3.IntegrityError)


def test_transaction_rolls_back_on_exception(db: Database) -> None:
    before = db.connection.execute("SELECT count(*) FROM sources").fetchone()[0]

    def bad() -> None:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO sources (source_type, title) VALUES (?, ?)",
                ("TD", "will be rolled back"),
            )
            # FK violation aborts the transaction.
            conn.execute(
                "INSERT INTO requirements (source_id, requirement_text) VALUES (?, ?)",
                (9999, "bad source"),
            )

    expect_error(bad, sqlite3.IntegrityError)
    after = db.connection.execute("SELECT count(*) FROM sources").fetchone()[0]
    assert before == after


def test_transaction_commits_on_success(db: Database) -> None:
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO sources (source_type, title) VALUES (?, ?)", ("TD", "committed")
        )
        sid = cur.lastrowid
        assert sid and sid > 0

    row = db.connection.execute(
        "SELECT id, title FROM sources WHERE id = ?", (sid,)
    ).fetchone()
    assert row is not None
    assert row[0] == sid
    assert row[1] == "committed"


def test_row_factory_is_sqlite3_row(db: Database) -> None:
    with db.transaction() as conn:
        conn.execute("INSERT INTO sources (source_type, title) VALUES ('TD','x')")
    row = db.connection.execute("SELECT id FROM sources LIMIT 1").fetchone()
    assert isinstance(row, sqlite3.Row)


def test_unique_index_on_assessments_pair(db: Database) -> None:
    with db.transaction() as conn:
        s = conn.execute(
            "INSERT INTO sources (source_type, title) VALUES ('TD','t') RETURNING id"
        ).fetchone()
        sid = s[0]
        a = conn.execute(
            "INSERT INTO architectures (name, version) VALUES ('A','1') RETURNING id"
        ).fetchone()
        aid = a[0]
        r = conn.execute(
            "INSERT INTO requirements (source_id, requirement_text) "
            "VALUES (?, 'r') RETURNING id",
            (sid,),
        ).fetchone()
        rid = r[0]
        conn.execute(
            "INSERT INTO assessments (requirement_id, architecture_id, result, rationale) "
            "VALUES (?, ?, 'COMPLIANT', 'first')",
            (rid, aid),
        )

    def bad() -> None:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO assessments (requirement_id, architecture_id, result, rationale) "
                "VALUES (?, ?, 'INCOMPLIANT', 'second')",
                (rid, aid),
            )

    expect_error(bad, sqlite3.IntegrityError)


def test_assessment_facts_relation_type_check(db: Database) -> None:
    """Inserting an invalid relation_type must fail and roll back cleanly."""
    with db.transaction() as conn:
        s = conn.execute(
            "INSERT INTO sources (source_type, title) VALUES ('TD','t') RETURNING id"
        ).fetchone()
        a = conn.execute(
            "INSERT INTO architectures (name, version) VALUES ('B','1') RETURNING id"
        ).fetchone()
        aid = a[0]
        rid = conn.execute(
            "INSERT INTO requirements (source_id, requirement_text) "
            "VALUES (?, 'r') RETURNING id",
            (s[0],),
        ).fetchone()[0]
        fid = conn.execute(
            "INSERT INTO facts (architecture_id, source_id, fact_text) "
            "VALUES (?, ?, 'f') RETURNING id",
            (aid, s[0]),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO assessments (requirement_id, architecture_id, result, rationale) "
            "VALUES (?, ?, 'COMPLIANT', 'ok')",
            (rid, aid),
        )
        aid_asmt = conn.execute(
            "SELECT id FROM assessments WHERE requirement_id=? AND architecture_id=?",
            (rid, aid),
        ).fetchone()[0]

    def bad() -> None:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO assessment_facts (assessment_id, fact_id, relation_type) "
                "VALUES (?, ?, 'NOT_A_VALID_RELATION')",
                (aid_asmt, fid),
            )

    expect_error(bad, sqlite3.IntegrityError)
    n = db.connection.execute("SELECT count(*) FROM assessment_facts").fetchone()[0]
    assert n == 0


def test_valid_assessment_fact_insert_succeeds(db: Database) -> None:
    with db.transaction() as conn:
        s = conn.execute(
            "INSERT INTO sources (source_type, title) VALUES ('TD','t') RETURNING id"
        ).fetchone()
        a = conn.execute(
            "INSERT INTO architectures (name, version) VALUES ('C','1') RETURNING id"
        ).fetchone()
        aid = a[0]
        rid = conn.execute(
            "INSERT INTO requirements (source_id, requirement_text) "
            "VALUES (?, 'r') RETURNING id",
            (s[0],),
        ).fetchone()[0]
        fid1 = conn.execute(
            "INSERT INTO facts (architecture_id, source_id, fact_text) "
            "VALUES (?, ?, 'fact-A') RETURNING id",
            (aid, s[0]),
        ).fetchone()[0]
        fid2 = conn.execute(
            "INSERT INTO facts (architecture_id, source_id, fact_text) "
            "VALUES (?, ?, 'fact-B') RETURNING id",
            (aid, s[0]),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO assessments (requirement_id, architecture_id, result, rationale) "
            "VALUES (?, ?, 'COMPLIANT', 'ok')",
            (rid, aid),
        )
        aid_asmt = conn.execute(
            "SELECT id FROM assessments WHERE requirement_id=? AND architecture_id=?",
            (rid, aid),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO assessment_facts (assessment_id, fact_id, relation_type) "
            "VALUES (?, ?, 'SUPPORTS')",
            (aid_asmt, fid1),
        )
        conn.execute(
            "INSERT INTO assessment_facts (assessment_id, fact_id, relation_type) "
            "VALUES (?, ?, 'CONTEXT')",
            (aid_asmt, fid2),
        )

    n = db.connection.execute("SELECT count(*) FROM assessment_facts").fetchone()[0]
    assert n == 2
    relations = {
        r[0]
        for r in db.connection.execute("SELECT DISTINCT relation_type FROM assessment_facts")
    }
    assert relations == {"SUPPORTS", "CONTEXT"}
