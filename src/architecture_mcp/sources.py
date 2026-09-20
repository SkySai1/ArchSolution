"""Domain operations and MCP tools for sources, НПА extensions, and architectures.

Public API (importable from this module):
- create_source / get_source / list_sources
- attach_npa / get_npa
- create_architecture / get_architecture / list_architectures
- register(mcp, db) — exposes the MCP tools

Every public function returns a standardized envelope:
- success → ``ok(...)`` (see ``models.ok``)
- error   → ``err(...)``, ``not_found(...)``, ``bad_request(...)``, ``conflict(...)``

Tools stay thin: they only do argument coercion + call the underlying function.
"""
from __future__ import annotations

import sqlite3

from . import models as M
from .db import Database

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _one_of(value: str, allowed: set[str], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"bad {field} {value!r}; expected one of {sorted(allowed)}")
    return value


def _non_empty(text: str, field: str) -> str:
    if not text or not text.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return text


def _page(limit: int | None, offset: int | None) -> tuple[int, int]:
    lim = min(max(int(limit or M.DEFAULT_LIMIT), 1), M.MAX_LIMIT)
    off = max(int(offset or 0), 0)
    return lim, off


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def create_source(
    db: Database,
    *,
    source_type: str,
    title: str,
    version: str | None = None,
    file_path: str | None = None,
    status: str = M.SourceStatus.ACTIVE.value,
) -> dict:
    try:
        _one_of(source_type, {k.value for k in M.SourceKind}, "source_type")
        _non_empty(title, "title")
        _one_of(status, {k.value for k in M.SourceStatus}, "status")
    except ValueError as exc:
        return M.bad_request(str(exc))

    try:
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO sources (source_type, title, version, file_path, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_type, title, version, file_path, status),
            )
    except sqlite3.IntegrityError as exc:
        return M.conflict(str(exc))

    row = db.connection.execute(
        "SELECT * FROM sources WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return M.ok(**dict(row))


def get_source(db: Database, source_id: int) -> dict:
    row = db.connection.execute(
        "SELECT * FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    if row is None:
        return M.not_found("source", id=source_id)
    return M.ok(**dict(row))


def list_sources(
    db: Database, *, limit: int | None = None, offset: int | None = None
) -> dict:
    lim, off = _page(limit, offset)
    rows = db.connection.execute(
        "SELECT * FROM sources ORDER BY id DESC LIMIT ? OFFSET ?", (lim, off)
    ).fetchall()
    return M.ok(items=[dict(r) for r in rows], limit=lim, offset=off)


# ---------------------------------------------------------------------------
# НПА (1:1 extension on sources)
# ---------------------------------------------------------------------------


def attach_npa(
    db: Database,
    *,
    source_id: int,
    document_type: str,
    number: str | None = None,
    issuer: str | None = None,
    status: str = M.NpaStatus.ACTIVE.value,
) -> dict:
    """Attach (or replace) the НПА extension for an existing NPA-type source."""
    try:
        _non_empty(document_type, "document_type")
        _one_of(status, {k.value for k in M.NpaStatus}, "npa status")
    except ValueError as exc:
        return M.bad_request(str(exc))

    srow = db.connection.execute(
        "SELECT source_type FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    if srow is None:
        return M.not_found("source", id=source_id)
    if srow[0] != M.SourceKind.NPA.value:
        return M.bad_request(
            f"source {source_id} is {srow[0]!r}; НПА attachment only allowed "
            f"for source_type='NPA'"
        )

    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO npa (source_id, document_type, number, issuer, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_id, document_type, number, issuer, status),
            )
    except sqlite3.IntegrityError as exc:
        return M.conflict(str(exc))

    out = db.connection.execute(
        "SELECT * FROM npa WHERE source_id = ?", (source_id,)
    ).fetchone()
    if out is None:
        return M.err("npa row missing after attach", code="INTERNAL")
    return M.ok(**dict(out))


def get_npa(db: Database, source_id: int) -> dict:
    row = db.connection.execute(
        "SELECT * FROM npa WHERE source_id = ?", (source_id,)
    ).fetchone()
    if row is None:
        return M.not_found("npa", source_id=source_id)
    return M.ok(**dict(row))


# ---------------------------------------------------------------------------
# Architectures
# ---------------------------------------------------------------------------


def create_architecture(
    db: Database,
    *,
    name: str,
    version: str | None = None,
    description: str | None = None,
    status: str = M.ArchitectureStatus.ACTIVE.value,
) -> dict:
    try:
        _non_empty(name, "name")
        _one_of(status, {k.value for k in M.ArchitectureStatus}, "status")
    except ValueError as exc:
        return M.bad_request(str(exc))

    try:
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO architectures (name, version, description, status) "
                "VALUES (?, ?, ?, ?)",
                (name, version, description, status),
            )
    except sqlite3.IntegrityError as exc:
        return M.conflict(str(exc))

    row = db.connection.execute(
        "SELECT * FROM architectures WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return M.ok(**dict(row))


def get_architecture(db: Database, architecture_id: int) -> dict:
    row = db.connection.execute(
        "SELECT * FROM architectures WHERE id = ?", (architecture_id,)
    ).fetchone()
    if row is None:
        return M.not_found("architecture", id=architecture_id)
    return M.ok(**dict(row))


def list_architectures(
    db: Database, *, limit: int | None = None, offset: int | None = None
) -> dict:
    lim, off = _page(limit, offset)
    rows = db.connection.execute(
        "SELECT * FROM architectures ORDER BY id DESC LIMIT ? OFFSET ?", (lim, off)
    ).fetchall()
    return M.ok(items=[dict(r) for r in rows], limit=lim, offset=off)


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def register(mcp, db: Database) -> None:
    """Expose source / НПА / architecture tools on the MCP server."""

    @mcp.tool()
    def source_create(
        source_type: str,
        title: str,
        version: str | None = None,
        file_path: str | None = None,
        status: str = M.SourceStatus.ACTIVE.value,
    ) -> dict:
        """Create a source document (НПА / ТЗ / architecture doc / standard / internal)."""
        return create_source(db, source_type=source_type, title=title,
                             version=version, file_path=file_path, status=status)

    @mcp.tool()
    def source_get(source_id: int) -> dict:
        """Fetch a source row by id."""
        return get_source(db, source_id)

    @mcp.tool()
    def source_list(limit: int = 50, offset: int = 0) -> dict:
        """List sources (newest-first) with limit/offset paging."""
        return list_sources(db, limit=limit, offset=offset)

    @mcp.tool()
    def npa_attach(
        source_id: int,
        document_type: str,
        number: str | None = None,
        issuer: str | None = None,
        status: str = M.NpaStatus.ACTIVE.value,
    ) -> dict:
        """Attach (or replace) the НПА extension row for a source."""
        return attach_npa(db, source_id=source_id, document_type=document_type,
                          number=number, issuer=issuer, status=status)

    @mcp.tool()
    def npa_get(source_id: int) -> dict:
        """Fetch the НПА extension row for a source by source id."""
        return get_npa(db, source_id)

    @mcp.tool()
    def architecture_create(
        name: str,
        version: str | None = None,
        description: str | None = None,
        status: str = M.ArchitectureStatus.ACTIVE.value,
    ) -> dict:
        """Register an architecture under analysis."""
        return create_architecture(db, name=name, version=version,
                                   description=description, status=status)

    @mcp.tool()
    def architecture_get(architecture_id: int) -> dict:
        """Fetch an architecture by id."""
        return get_architecture(db, architecture_id)

    @mcp.tool()
    def architecture_list(limit: int = 50, offset: int = 0) -> dict:
        """List architectures (newest-first) with limit/offset paging."""
        return list_architectures(db, limit=limit, offset=offset)
