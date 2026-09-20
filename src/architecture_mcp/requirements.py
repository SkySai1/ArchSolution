"""Domain operations and MCP tools for atomic requirements.

Public API (importable from this module):
- create_requirement / get_requirement / list_requirements
- search_requirements / update_requirement
- list_requirement_categories (M:N read side)
- register(mcp, db) — exposes the MCP tools

Every public function returns a standardized envelope:
- success → ``ok(...)`` (see ``models.ok``)
- error   → ``err(...)`` / ``not_found(...)`` / ``bad_request(...)`` / ``conflict(...)``
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


# ---------------------------------------------------------------------------
# Domain operations
# ---------------------------------------------------------------------------


def create_requirement(
    db: Database,
    *,
    source_id: int,
    requirement_text: str,
    source_locator: str | None = None,
    source_quote: str | None = None,
    normalized_text: str | None = None,
    status: str = M.RequirementStatus.ACTIVE.value,
) -> dict:
    """Create one atomic requirement bound to an existing source."""
    try:
        _non_empty(requirement_text, "requirement_text")
        _one_of(status, {k.value for k in M.RequirementStatus}, "status")
        _require_source(db, source_id)
    except ValueError as exc:
        return M.bad_request(str(exc))
    except LookupError:
        return M.not_found("source", id=source_id)

    try:
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO requirements "
                "(source_id, source_locator, source_quote, requirement_text, "
                " normalized_text, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source_id, source_locator, source_quote, requirement_text,
                 normalized_text, status),
            )
    except sqlite3.IntegrityError as exc:
        return M.conflict(str(exc))

    row = db.connection.execute(
        "SELECT * FROM requirements WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return M.ok(**dict(row))


def get_requirement(db: Database, requirement_id: int) -> dict:
    row = db.connection.execute(
        "SELECT * FROM requirements WHERE id = ?", (requirement_id,)
    ).fetchone()
    if row is None:
        return M.not_found("requirement", id=requirement_id)
    return M.ok(**dict(row))


def list_requirements(
    db: Database,
    *,
    source_id: int | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    lim, off = _page(limit, offset)
    sql = "SELECT * FROM requirements"
    args: list[object] = []
    if source_id is not None:
        sql += " WHERE source_id = ?"
        args.append(source_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    rows = db.connection.execute(sql, (*args, lim, off)).fetchall()
    return M.ok(items=[dict(r) for r in rows], limit=lim, offset=off)


def search_requirements(
    db: Database,
    *,
    query: str,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    """Case-insensitive substring search over requirement text."""
    lim, off = _page(limit, offset)
    needle = f"%{_escape_like(query.strip())}%"
    rows = db.connection.execute(
        "SELECT * FROM requirements "
        "WHERE ilower(requirement_text) LIKE ilower(?) ESCAPE '\\' "
        "   OR ilower(coalesce(normalized_text, '')) LIKE ilower(?) ESCAPE '\\' "
        "ORDER BY id DESC LIMIT ? OFFSET ?",
        (needle, needle, lim, off),
    ).fetchall()
    return M.ok(items=[dict(r) for r in rows], limit=lim, offset=off)


def update_requirement(
    db: Database,
    requirement_id: int,
    *,
    requirement_text: str | None = None,
    normalized_text: str | None = None,
    source_locator: str | None = None,
    source_quote: str | None = None,
    status: str | None = None,
) -> dict:
    """Partial update of a requirement; only provided fields are changed.

    ADR-001 §7: ``source_quote`` is the original evidence and is immutable
    through the generic update path. Passing it is rejected with a
    BAD_REQUEST; use ``correct_requirement_quote`` to fix a wrongly-recorded
    quote.
    """
    if source_quote is not None:
        return M.bad_request(
            "source_quote is immutable via requirement_update; "
            "use correct_requirement_quote to change it",
        )
    fields: dict[str, object] = {}
    if requirement_text is not None:
        try:
            _non_empty(requirement_text, "requirement_text")
        except ValueError as exc:
            return M.bad_request(str(exc))
        fields["requirement_text"] = requirement_text
    if normalized_text is not None:
        fields["normalized_text"] = normalized_text
    if source_locator is not None:
        fields["source_locator"] = source_locator
    if status is not None:
        try:
            _one_of(status, {k.value for k in M.RequirementStatus}, "status")
        except ValueError as exc:
            return M.bad_request(str(exc))
        fields["status"] = status

    if not fields:
        return M.bad_request("provide at least one field to update")

    if not db.connection.execute(
        "SELECT 1 FROM requirements WHERE id = ?", (requirement_id,)
    ).fetchone():
        return M.not_found("requirement", id=requirement_id)

    sets = ", ".join(f"{k} = ?" for k in fields)
    try:
        with db.transaction() as conn:
            conn.execute(
                f"UPDATE requirements SET {sets} WHERE id = ?",
                (*fields.values(), requirement_id),
            )
    except sqlite3.IntegrityError as exc:
        return M.conflict(str(exc))

    row = db.connection.execute(
        "SELECT * FROM requirements WHERE id = ?", (requirement_id,)
    ).fetchone()
    return M.ok(**dict(row))


def correct_requirement_quote(
    db: Database,
    requirement_id: int,
    *,
    source_quote: str,
) -> dict:
    """ADR-001 §7: fix an original quote only through this dedicated
    operation. ``source_quote`` is the evidence of provenance and must not
    be changed by the generic update path.
    """
    if not source_quote or not source_quote.strip():
        return M.bad_request("source_quote must be a non-empty string")
    if not db.connection.execute(
        "SELECT 1 FROM requirements WHERE id = ?", (requirement_id,)
    ).fetchone():
        return M.not_found("requirement", id=requirement_id)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE requirements SET source_quote = ? WHERE id = ?",
            (source_quote, requirement_id),
        )
    row = db.connection.execute(
        "SELECT * FROM requirements WHERE id = ?", (requirement_id,)
    ).fetchone()
    return M.ok(**dict(row))


def list_requirement_categories(db: Database, requirement_id: int) -> dict:
    """List categories attached to a requirement (M:N read side)."""
    if not db.connection.execute(
        "SELECT 1 FROM requirements WHERE id = ?", (requirement_id,)
    ).fetchone():
        return M.not_found("requirement", id=requirement_id)
    rows = db.connection.execute(
        "SELECT c.*, rc.confidence "
        "FROM requirement_categories rc "
        "JOIN categories c ON c.id = rc.category_id "
        "WHERE rc.requirement_id = ? "
        "ORDER BY c.id",
        (requirement_id,),
    ).fetchall()
    return M.ok(items=[dict(r) for r in rows])


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def register(mcp, db: Database) -> None:
    """Expose requirement tools on the MCP server."""

    @mcp.tool()
    def requirement_create(
        source_id: int,
        requirement_text: str,
        source_locator: str | None = None,
        source_quote: str | None = None,
        normalized_text: str | None = None,
        status: str = M.RequirementStatus.ACTIVE.value,
    ) -> dict:
        """Create an atomic requirement from a source document."""
        return create_requirement(
            db, source_id=source_id, requirement_text=requirement_text,
            source_locator=source_locator, source_quote=source_quote,
            normalized_text=normalized_text, status=status,
        )

    @mcp.tool()
    def requirement_get(requirement_id: int) -> dict:
        """Fetch a requirement by id."""
        return get_requirement(db, requirement_id)

    @mcp.tool()
    def requirement_list(
        source_id: int | None = None, limit: int = 50, offset: int = 0
    ) -> dict:
        """List requirements, optionally filtered by source, newest-first."""
        return list_requirements(db, source_id=source_id, limit=limit, offset=offset)

    @mcp.tool()
    def requirement_search(query: str, limit: int = 50, offset: int = 0) -> dict:
        """Substring search over requirement text (case-insensitive)."""
        return search_requirements(db, query=query, limit=limit, offset=offset)

    @mcp.tool()
    def requirement_update(
        requirement_id: int,
        requirement_text: str | None = None,
        normalized_text: str | None = None,
        source_locator: str | None = None,
        source_quote: str | None = None,
        status: str | None = None,
    ) -> dict:
        """Partially update a requirement (only provided fields)."""
        return update_requirement(
            db, requirement_id, requirement_text=requirement_text,
            normalized_text=normalized_text, source_locator=source_locator,
            source_quote=source_quote, status=status,
        )

    @mcp.tool()
    def requirement_correct_quote(
        requirement_id: int,
        source_quote: str,
    ) -> dict:
        """Correct the original source_quote (dedicated ADR-001 §7 operation).

        Requirement_update refuses to change source_quote; use this tool
        to fix a wrongly-recorded quote.
        """
        return correct_requirement_quote(
            db, requirement_id, source_quote=source_quote
        )

    @mcp.tool()
    def requirement_categories(requirement_id: int) -> dict:
        """List the categories attached to a requirement."""
        return list_requirement_categories(db, requirement_id)
