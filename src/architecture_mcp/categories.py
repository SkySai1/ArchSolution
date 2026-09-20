"""Domain operations and MCP tools for the unified category directory.

Public API (importable from this module):
- create_category / get_category / list_categories / search_categories
- assign_category / unassign_category
- register(mcp, db) — exposes the MCP tools

``assign_category`` is the single cross-domain write: it validates the
referenced requirement/fact row and inserts the M:N link. This is the one
allowed cross-module interaction (same write path, per AGENTS.md §3).
Every public function returns a standardized envelope (see ``models``).
"""
from __future__ import annotations

import sqlite3

from . import models as M
from .db import Database

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_REQUIREMENT_OK_SCOPES = {"REQUIREMENT", "BOTH"}
_FACT_OK_SCOPES = {"FACT", "BOTH"}


def _non_empty(text: str, field: str) -> str:
    if not text or not text.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return text


def _one_of(value: str, allowed: set[str], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"bad {field} {value!r}; expected one of {sorted(allowed)}")
    return value


def _confidence(conf: float) -> float:
    c = float(conf)
    if not 0.0 <= c <= 1.0:
        raise ValueError(f"confidence must be in [0.0, 1.0], got {c}")
    return c


def _page(limit: int | None, offset: int | None) -> tuple[int, int]:
    lim = min(max(int(limit or M.DEFAULT_LIMIT), 1), M.MAX_LIMIT)
    off = max(int(offset or 0), 0)
    return lim, off


def _escape_like(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _require(db: Database, table: str, row_id: int, label: str) -> None:
    if not db.connection.execute(
        f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)  # noqa: S608 (internal table)
    ).fetchone():
        raise LookupError(f"{label} {row_id} does not exist")


# ---------------------------------------------------------------------------
# Category directory
# ---------------------------------------------------------------------------


def create_category(
    db: Database,
    *,
    name: str,
    description: str | None = None,
    scope: str = M.CategoryScope.BOTH.value,
    parent_id: int | None = None,
    status: str = M.CategoryStatus.ACTIVE.value,
) -> dict:
    """Create a category (optionally as a child of another category)."""
    try:
        _non_empty(name, "name")
        _one_of(scope, {k.value for k in M.CategoryScope}, "scope")
        _one_of(status, {k.value for k in M.CategoryStatus}, "status")
        if parent_id is not None:
            _require(db, "categories", parent_id, "parent category")
    except ValueError as exc:
        return M.bad_request(str(exc))
    except LookupError:
        return M.not_found("category", id=parent_id)

    try:
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO categories (name, description, scope, parent_id, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, description, scope, parent_id, status),
            )
    except sqlite3.IntegrityError as exc:
        return M.conflict(str(exc))

    row = db.connection.execute("SELECT * FROM categories WHERE id = ?", (cur.lastrowid,)).fetchone()
    return M.ok(**dict(row))


def get_category(db: Database, category_id: int) -> dict:
    row = db.connection.execute(
        "SELECT c.*, p.name AS parent_name "
        "FROM categories c LEFT JOIN categories p ON p.id = c.parent_id "
        "WHERE c.id = ?",
        (category_id,),
    ).fetchone()
    if row is None:
        return M.not_found("category", id=category_id)
    return M.ok(**dict(row))


def list_categories(
    db: Database,
    *,
    scope: str | None = None,
    parent_id: int | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    lim, off = _page(limit, offset)
    sql = "SELECT c.*, p.name AS parent_name FROM categories c " \
          "LEFT JOIN categories p ON p.id = c.parent_id WHERE 1=1"
    args: list[object] = []
    if scope is not None:
        if scope not in M.CategoryScope._value2member_map_:
            return M.bad_request(f"bad scope {scope!r}")
        sql += " AND c.scope = ?"
        args.append(scope)
    if parent_id is not None:
        sql += " AND c.parent_id = ?"
        args.append(parent_id)
    sql += " ORDER BY c.id LIMIT ? OFFSET ?"
    args += [lim, off]
    rows = db.connection.execute(sql, args).fetchall()
    return M.ok(items=[dict(r) for r in rows], limit=lim, offset=off)


def search_categories(db: Database, *, query: str, limit: int = 50, offset: int = 0) -> dict:
    """Case-insensitive substring search over category names + descriptions.

    Use this BEFORE creating a new category: if a suitable category already
    exists, assign it instead of creating a duplicate."""
    lim, off = _page(limit, offset)
    needle = f"%{_escape_like(query.strip())}%"
    rows = db.connection.execute(
        "SELECT c.*, p.name AS parent_name "
        "FROM categories c LEFT JOIN categories p ON p.id = c.parent_id "
        "WHERE (ilower(c.name) LIKE ilower(?) ESCAPE '\\' "
        "   OR ilower(coalesce(c.description, '')) LIKE ilower(?) ESCAPE '\\') "
        "ORDER BY c.id LIMIT ? OFFSET ?",
        (needle, needle, lim, off),
    ).fetchall()
    return M.ok(items=[dict(r) for r in rows], limit=lim, offset=off)


# ---------------------------------------------------------------------------
# Category assignment (M:N write)
# ---------------------------------------------------------------------------


def assign_category(
    db: Database,
    *,
    category_id: int,
    requirement_id: int | None = None,
    fact_id: int | None = None,
    confidence: float = 1.0,
) -> dict:
    """Attach a category to exactly one requirement or one fact.

    Scope rules: a REQUIREMENT-scoped category is rejected on a fact and
    vice versa; BOTH/ASSESSMENT categories are accepted on both sides.
    Re-assignment upserts the existing row (same pair, new confidence).
    """
    if (requirement_id is None) == (fact_id is None):
        return M.bad_request("provide exactly one of requirement_id or fact_id")

    try:
        conf = _confidence(confidence)
    except ValueError as exc:
        return M.bad_request(str(exc))

    target = "REQUIREMENT" if requirement_id is not None else "FACT"

    if not db.connection.execute("SELECT 1 FROM categories WHERE id = ?", (category_id,)).fetchone():
        return M.not_found("category", id=category_id)
    try:
        if requirement_id is not None:
            _require(db, "requirements", requirement_id, "requirement")
        else:
            _require(db, "facts", fact_id, "fact")
    except LookupError:
        kind = "requirement" if requirement_id is not None else "fact"
        return M.not_found(kind, id=requirement_id if requirement_id is not None else fact_id)

    scope = db.connection.execute(
        "SELECT scope FROM categories WHERE id = ?", (category_id,)
    ).fetchone()[0]
    allowed = _REQUIREMENT_OK_SCOPES if requirement_id is not None else _FACT_OK_SCOPES
    if scope not in allowed:
        return M.bad_request(
            f"category {category_id} has scope {scope!r}; "
            f"cannot be assigned to a {target.lower()}"
        )

    table = "requirement_categories" if requirement_id is not None else "fact_categories"
    fk = "requirement_id" if requirement_id is not None else "fact_id"
    fk_value = requirement_id if requirement_id is not None else fact_id

    try:
        with db.transaction() as conn:
            conn.execute(
                f"INSERT INTO {table} ({fk}, category_id, confidence) "
                f"VALUES (?, ?, ?) "
                f"ON CONFLICT({fk}, category_id) "
                f"DO UPDATE SET confidence = excluded.confidence",
                (fk_value, category_id, conf),
            )
    except sqlite3.IntegrityError as exc:
        return M.conflict(str(exc))

    row = db.connection.execute(
        f"SELECT c.*, {fk} AS target_id, rc.confidence "
        f"FROM {table} rc JOIN categories c ON c.id = rc.category_id "
        f"WHERE rc.{fk} = ? AND rc.category_id = ?",
        (fk_value, category_id),
    ).fetchone()
    return M.ok(**dict(row))


def unassign_category(
    db: Database,
    *,
    category_id: int,
    requirement_id: int | None = None,
    fact_id: int | None = None,
) -> dict:
    """Remove a category link from a requirement or fact (idempotent)."""
    if (requirement_id is None) == (fact_id is None):
        return M.bad_request("provide exactly one of requirement_id or fact_id")

    table = "requirement_categories" if requirement_id is not None else "fact_categories"
    fk = "requirement_id" if requirement_id is not None else "fact_id"
    fk_value = requirement_id if requirement_id is not None else fact_id

    try:
        with db.transaction() as conn:
            cur = conn.execute(
                f"DELETE FROM {table} WHERE {fk} = ? AND category_id = ?",
                (fk_value, category_id),
            )
    except sqlite3.IntegrityError as exc:  # pragma: no cover - DELETE cannot violate FK
        return M.conflict(str(exc))

    if cur.rowcount == 0:
        return M.err(f"no assignment found for {fk}={fk_value}, category={category_id}",
                     code="NOT_FOUND")
    return M.ok(deleted=True)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def register(mcp, db: Database) -> None:
    """Expose category-directory tools on the MCP server."""

    @mcp.tool()
    def category_create(
        name: str,
        description: str | None = None,
        scope: str = M.CategoryScope.BOTH.value,
        parent_id: int | None = None,
        status: str = M.CategoryStatus.ACTIVE.value,
    ) -> dict:
        """Create a category (optionally under a parent). Name is unique per parent."""
        return create_category(db, name=name, description=description, scope=scope,
                               parent_id=parent_id, status=status)

    @mcp.tool()
    def category_get(category_id: int) -> dict:
        """Fetch a category by id (includes parent name)."""
        return get_category(db, category_id)

    @mcp.tool()
    def category_list(
        scope: str | None = None,
        parent_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """List the category directory, optionally filtered by scope/parent."""
        return list_categories(db, scope=scope, parent_id=parent_id,
                               limit=limit, offset=offset)

    @mcp.tool()
    def category_search(query: str, limit: int = 50, offset: int = 0) -> dict:
        """Search existing categories by name/description — call this BEFORE creating a new one."""
        return search_categories(db, query=query, limit=limit, offset=offset)

    @mcp.tool()
    def category_assign(
        category_id: int,
        requirement_id: int | None = None,
        fact_id: int | None = None,
        confidence: float = 1.0,
    ) -> dict:
        """Attach a category to one requirement OR one fact (upsert)."""
        return assign_category(db, category_id=category_id,
                               requirement_id=requirement_id, fact_id=fact_id,
                               confidence=confidence)

    @mcp.tool()
    def category_unassign(
        category_id: int,
        requirement_id: int | None = None,
        fact_id: int | None = None,
    ) -> dict:
        """Remove a category from a requirement or fact."""
        return unassign_category(db, category_id=category_id,
                                 requirement_id=requirement_id, fact_id=fact_id)
