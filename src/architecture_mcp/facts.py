"""Domain operations and MCP tools for atomic architectural facts.

Public API (importable from this module):
- create_fact / get_fact / list_facts
- search_facts / update_fact
- list_fact_categories (M:N read side)
- register(mcp, db) — exposes the MCP tools

A fact always belongs to exactly one architecture (``architecture_id`` is
mandatory) and is sourced from a document (``source_id`` is mandatory).
Every public function returns a standardized envelope (see ``models``).
"""
from __future__ import annotations

import sqlite3

from . import models as M
from .db import Database

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _non_empty(text: str, field: str) -> str:
    if not text or not text.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return text


def _one_of(value: str, allowed: set[str], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"bad {field} {value!r}; expected one of {sorted(allowed)}")
    return value


def _page(limit: int | None, offset: int | None) -> tuple[int, int]:
    lim = min(max(int(limit or M.DEFAULT_LIMIT), 1), M.MAX_LIMIT)
    off = max(int(offset or 0), 0)
    return lim, off


def _escape_like(query: str) -> str:
    """Escape LIKE wildcards so search is a literal substring match."""
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _require_source(db: Database, source_id: int) -> None:
    if not db.connection.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone():
        raise LookupError(f"source {source_id} does not exist")


def _require_architecture(db: Database, architecture_id: int) -> None:
    if not db.connection.execute(
        "SELECT 1 FROM architectures WHERE id = ?", (architecture_id,)
    ).fetchone():
        raise LookupError(f"architecture {architecture_id} does not exist")


# ---------------------------------------------------------------------------
# Domain operations
# ---------------------------------------------------------------------------


def create_fact(
    db: Database,
    *,
    architecture_id: int,
    source_id: int,
    fact_text: str,
    source_locator: str | None = None,
    source_quote: str | None = None,
    normalized_text: str | None = None,
    status: str = M.FactStatus.ACTIVE.value,
) -> dict:
    """Create one atomic architectural fact bound to an architecture and a source."""
    try:
        _non_empty(fact_text, "fact_text")
        _one_of(status, {k.value for k in M.FactStatus}, "status")
        _require_source(db, source_id)
        _require_architecture(db, architecture_id)
    except ValueError as exc:
        return M.bad_request(str(exc))
    except LookupError as exc:
        kind, key = exc.args[0].split()[0], exc.args[0].split()[1]
        return M.not_found(kind, id=int(key))

    try:
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO facts "
                "(architecture_id, source_id, source_locator, source_quote, "
                " fact_text, normalized_text, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (architecture_id, source_id, source_locator, source_quote,
                 fact_text, normalized_text, status),
            )
    except sqlite3.IntegrityError as exc:
        return M.conflict(str(exc))

    row = db.connection.execute("SELECT * FROM facts WHERE id = ?", (cur.lastrowid,)).fetchone()
    return M.ok(**dict(row))


def get_fact(db: Database, fact_id: int) -> dict:
    row = db.connection.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if row is None:
        return M.not_found("fact", id=fact_id)
    return M.ok(**dict(row))


def list_facts(
    db: Database,
    *,
    architecture_id: int | None = None,
    source_id: int | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    lim, off = _page(limit, offset)
    sql = "SELECT * FROM facts"
    clauses: list[str] = []
    args: list[object] = []
    if architecture_id is not None:
        clauses.append("architecture_id = ?")
        args.append(architecture_id)
    if source_id is not None:
        clauses.append("source_id = ?")
        args.append(source_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    rows = db.connection.execute(sql, (*args, lim, off)).fetchall()
    return M.ok(items=[dict(r) for r in rows], limit=lim, offset=off)


def search_facts(
    db: Database,
    *,
    query: str,
    architecture_id: int | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    """Case-insensitive substring search over fact text."""
    lim, off = _page(limit, offset)
    needle = f"%{_escape_like(query.strip())}%"
    sql = (
        "SELECT * FROM facts "
        "WHERE ilower(fact_text) LIKE ilower(?) ESCAPE '\\' "
        "   OR ilower(coalesce(normalized_text, '')) LIKE ilower(?) ESCAPE '\\'"
    )
    args: list[object] = [needle, needle]
    if architecture_id is not None:
        sql += " AND architecture_id = ?"
        args.append(architecture_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [lim, off]
    rows = db.connection.execute(sql, args).fetchall()
    return M.ok(items=[dict(r) for r in rows], limit=lim, offset=off)


def update_fact(
    db: Database,
    fact_id: int,
    *,
    fact_text: str | None = None,
    normalized_text: str | None = None,
    source_locator: str | None = None,
    source_quote: str | None = None,
    status: str | None = None,
) -> dict:
    """Partial update of a fact; only provided fields are changed."""
    fields: dict[str, object] = {}
    if fact_text is not None:
        try:
            _non_empty(fact_text, "fact_text")
        except ValueError as exc:
            return M.bad_request(str(exc))
        fields["fact_text"] = fact_text
    if normalized_text is not None:
        fields["normalized_text"] = normalized_text
    if source_locator is not None:
        fields["source_locator"] = source_locator
    if source_quote is not None:
        fields["source_quote"] = source_quote
    if status is not None:
        try:
            _one_of(status, {k.value for k in M.FactStatus}, "status")
        except ValueError as exc:
            return M.bad_request(str(exc))
        fields["status"] = status

    if not fields:
        return M.bad_request("provide at least one field to update")

    if not db.connection.execute("SELECT 1 FROM facts WHERE id = ?", (fact_id,)).fetchone():
        return M.not_found("fact", id=fact_id)

    sets = ", ".join(f"{k} = ?" for k in fields)
    try:
        with db.transaction() as conn:
            conn.execute(f"UPDATE facts SET {sets} WHERE id = ?", (*fields.values(), fact_id))
    except sqlite3.IntegrityError as exc:
        return M.conflict(str(exc))

    row = db.connection.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    return M.ok(**dict(row))


def list_fact_categories(db: Database, fact_id: int) -> dict:
    """List categories attached to a fact (M:N read side)."""
    if not db.connection.execute("SELECT 1 FROM facts WHERE id = ?", (fact_id,)).fetchone():
        return M.not_found("fact", id=fact_id)
    rows = db.connection.execute(
        "SELECT c.*, fc.confidence "
        "FROM fact_categories fc "
        "JOIN categories c ON c.id = fc.category_id "
        "WHERE fc.fact_id = ? "
        "ORDER BY c.id",
        (fact_id,),
    ).fetchall()
    return M.ok(items=[dict(r) for r in rows])


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def register(mcp, db: Database) -> None:
    """Expose fact tools on the MCP server."""

    @mcp.tool()
    def fact_create(
        architecture_id: int,
        source_id: int,
        fact_text: str,
        source_locator: str | None = None,
        source_quote: str | None = None,
        normalized_text: str | None = None,
        status: str = M.FactStatus.ACTIVE.value,
    ) -> dict:
        """Create an atomic architectural fact for a specific architecture."""
        return create_fact(
            db, architecture_id=architecture_id, source_id=source_id,
            fact_text=fact_text, source_locator=source_locator,
            source_quote=source_quote, normalized_text=normalized_text, status=status,
        )

    @mcp.tool()
    def fact_get(fact_id: int) -> dict:
        """Fetch a fact by id."""
        return get_fact(db, fact_id)

    @mcp.tool()
    def fact_list(
        architecture_id: int | None = None,
        source_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """List facts, optionally filtered by architecture/source, newest-first."""
        return list_facts(
            db, architecture_id=architecture_id, source_id=source_id,
            limit=limit, offset=offset,
        )

    @mcp.tool()
    def fact_search(
        query: str,
        architecture_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Substring search over fact text (case-insensitive), optional architecture filter."""
        return search_facts(
            db, query=query, architecture_id=architecture_id,
            limit=limit, offset=offset,
        )

    @mcp.tool()
    def fact_update(
        fact_id: int,
        fact_text: str | None = None,
        normalized_text: str | None = None,
        source_locator: str | None = None,
        source_quote: str | None = None,
        status: str | None = None,
    ) -> dict:
        """Partially update a fact (only provided fields)."""
        return update_fact(
            db, fact_id, fact_text=fact_text, normalized_text=normalized_text,
            source_locator=source_locator, source_quote=source_quote, status=status,
        )

    @mcp.tool()
    def fact_categories(fact_id: int) -> dict:
        """List the categories attached to a fact."""
        return list_fact_categories(db, fact_id)
