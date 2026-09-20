"""Domain operations and MCP tools for compliance assessments.

An assessment is the verdict of exactly ONE requirement against exactly ONE
architecture. Its supporting evidence is a set of facts with an explicit
relation: ``SUPPORTS`` / ``CONTRADICTS`` / ``CONTEXT``.

Public API (importable from this module):
- create_assessment (with optional initial facts)
- get_assessment / list_assessments
- assess_attach_fact / assess_detach_fact
- assessment_facts (evidence read side)
- register(mcp, db) — exposes the MCP tools

Every public function returns a standardized envelope (see ``models``).
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


def _confidence(conf: float) -> float:
    c = float(conf)
    if not 0.0 <= c <= 1.0:
        raise ValueError(f"confidence must be in [0.0, 1.0], got {c}")
    return c


def _require_evidence_architecture(db: Database, architecture_id: int, fact_id: int) -> dict | None:
    """ADR-001 §4: a fact must belong to the same architecture as the
    assessment it evidences. Returns an error envelope on mismatch, else None.
    """
    row = db.connection.execute(
        "SELECT architecture_id FROM facts WHERE id = ?", (fact_id,)
    ).fetchone()
    if row is None:
        return M.not_found("fact", id=fact_id)
    if row["architecture_id"] != architecture_id:
        return M.bad_request(
            f"fact {fact_id} belongs to architecture {row['architecture_id']}, "
            f"but assessment belongs to architecture {architecture_id}",
            fact_id=fact_id,
        )
    return None


def _page(limit: int | None, offset: int | None) -> tuple[int, int]:
    lim = min(max(int(limit or M.DEFAULT_LIMIT), 1), M.MAX_LIMIT)
    off = max(int(offset or 0), 0)
    return lim, off


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------


def create_assessment(
    db: Database,
    *,
    requirement_id: int,
    architecture_id: int,
    result: str,
    rationale: str,
    confidence: float = 1.0,
    facts: list[dict] | None = None,
) -> dict:
    """Create a verdict for one (requirement, architecture) pair.

    ``facts`` optionally seeds the evidence list; each item is
    ``{"fact_id": int, "relation_type": "SUPPORTS" | "CONTRADICTS" | "CONTEXT"}``.
    The whole operation (verdict + evidence) is one atomic transaction.
    """
    check_result = _precheck(
        db, requirement_id=requirement_id, architecture_id=architecture_id,
        result=result, rationale=rationale, confidence=confidence, facts=facts,
    )
    if check_result is not None:
        return check_result

    try:
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO assessments "
                "(requirement_id, architecture_id, result, rationale, confidence) "
                "VALUES (?, ?, ?, ?, ?)",
                (requirement_id, architecture_id, result, rationale,
                 _confidence(confidence)),
            )
            assessment_id = cur.lastrowid
            for item in facts or []:
                conn.execute(
                    "INSERT INTO assessment_facts (assessment_id, fact_id, relation_type) "
                    "VALUES (?, ?, ?)",
                    (assessment_id, item["fact_id"], item["relation_type"]),
                )
    except sqlite3.IntegrityError as exc:
        msg = str(exc)
        if "UNIQUE" in msg:
            return M.conflict("assessment already exists for this requirement/architecture")
        if "FOREIGN KEY" in msg:
            return M.not_found("fact", id=None)
        return M.conflict(msg)

    row = db.connection.execute(
        "SELECT * FROM assessments WHERE id = ?", (assessment_id,)
    ).fetchone()
    return M.ok(**dict(row))


def _precheck(
    db: Database,
    *,
    requirement_id: int,
    architecture_id: int,
    result: str,
    rationale: str,
    confidence: float,
    facts: list[dict] | None,
) -> dict | None:
    """Validate all inputs up-front; return an error envelope or ``None`` if valid."""
    try:
        _one_of(result, {k.value for k in M.AssessmentResult}, "result")
        _non_empty(rationale, "rationale")
        _confidence(confidence)
        for item in facts or []:
            _one_of(str(item.get("relation_type", "")),
                    {k.value for k in M.RelationType}, "relation_type")
    except ValueError as exc:
        return M.bad_request(str(exc))

    if not db.connection.execute("SELECT 1 FROM requirements WHERE id = ?", (requirement_id,)).fetchone():
        return M.not_found("requirement", id=requirement_id)
    if not db.connection.execute("SELECT 1 FROM architectures WHERE id = ?", (architecture_id,)).fetchone():
        return M.not_found("architecture", id=architecture_id)
    for item in facts or []:
        # ADR-001 §4: each evidence fact must exist AND belong to the same
        # architecture as the assessment.
        err = _require_evidence_architecture(db, architecture_id, item.get("fact_id"))
        if err is not None:
            return err
    return None


def get_assessment(db: Database, assessment_id: int) -> dict:
    row = db.connection.execute(
        "SELECT * FROM assessments WHERE id = ?", (assessment_id,)
    ).fetchone()
    if row is None:
        return M.not_found("assessment", id=assessment_id)
    return M.ok(**dict(row))


def list_assessments(
    db: Database,
    *,
    requirement_id: int | None = None,
    architecture_id: int | None = None,
    result: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    lim, off = _page(limit, offset)
    sql, args = "SELECT * FROM assessments WHERE 1=1", []
    if requirement_id is not None:
        sql += " AND requirement_id = ?"
        args.append(requirement_id)
    if architecture_id is not None:
        sql += " AND architecture_id = ?"
        args.append(architecture_id)
    if result is not None:
        if result not in M.AssessmentResult._value2member_map_:
            return M.bad_request(f"bad result {result!r}")
        sql += " AND result = ?"
        args.append(result)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    rows = db.connection.execute(sql, (*args, lim, off)).fetchall()
    return M.ok(items=[dict(r) for r in rows], limit=lim, offset=off)


def attach_fact(
    db: Database,
    *,
    assessment_id: int,
    fact_id: int,
    relation_type: str,
) -> dict:
    """Link a fact to an assessment with an explicit relation (upsert).

    ADR-001 §4: the fact must belong to the same architecture as the
    assessment; the operation is rejected (no partial mutation) otherwise.
    """
    try:
        _one_of(relation_type, {k.value for k in M.RelationType}, "relation_type")
    except ValueError as exc:
        return M.bad_request(str(exc))

    asmt = db.connection.execute(
        "SELECT architecture_id FROM assessments WHERE id = ?", (assessment_id,)
    ).fetchone()
    if asmt is None:
        return M.not_found("assessment", id=assessment_id)

    err = _require_evidence_architecture(db, asmt["architecture_id"], fact_id)
    if err is not None:
        return err

    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO assessment_facts (assessment_id, fact_id, relation_type) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(assessment_id, fact_id) "
                "DO UPDATE SET relation_type = excluded.relation_type",
                (assessment_id, fact_id, relation_type),
            )
    except sqlite3.IntegrityError:
        return M.not_found("fact", id=fact_id)

    row = db.connection.execute(
        "SELECT af.*, f.fact_text "
        "FROM assessment_facts af JOIN facts f ON f.id = af.fact_id "
        "WHERE af.assessment_id = ? AND af.fact_id = ?",
        (assessment_id, fact_id),
    ).fetchone()
    return M.ok(**dict(row))


def detach_fact(db: Database, *, assessment_id: int, fact_id: int) -> dict:
    try:
        with db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM assessment_facts WHERE assessment_id = ? AND fact_id = ?",
                (assessment_id, fact_id),
            )
    except sqlite3.IntegrityError as exc:  # pragma: no cover
        return M.conflict(str(exc))
    if cur.rowcount == 0:
        return M.not_found("assessment fact link", assessment_id=assessment_id, fact_id=fact_id)
    return M.ok(deleted=True)


def update_assessment(
    db: Database,
    *,
    assessment_id: int,
    result: str | None = None,
    rationale: str | None = None,
    confidence: float | None = None,
    facts: list[dict] | None = None,
) -> dict:
    """Re-assess an existing assessment (ADR-001 §5).

    - ``result``, ``rationale``, ``confidence`` are updated when provided.
    - ``facts`` (optional): if a list is supplied it **replaces** the whole
      evidence set in a single transaction; if omitted, evidence is untouched.
    - The pair (requirement_id, architecture_id) is immutable here — to
      change the scope you create a new assessment for that pair.

    All changes are applied atomically: either the whole update lands or
    nothing does.
    """
    # --- validate up-front ---------------------------------------------------
    existing = db.connection.execute(
        "SELECT architecture_id FROM assessments WHERE id = ?", (assessment_id,)
    ).fetchone()
    if existing is None:
        return M.not_found("assessment", id=assessment_id)
    arch_id = existing["architecture_id"]

    if result is not None:
        try:
            _one_of(result, {k.value for k in M.AssessmentResult}, "result")
        except ValueError as exc:
            return M.bad_request(str(exc))
    if rationale is not None:
        try:
            _non_empty(rationale, "rationale")
        except ValueError as exc:
            return M.bad_request(str(exc))
    if confidence is not None:
        try:
            _confidence(confidence)
        except ValueError as exc:
            return M.bad_request(str(exc))

    if facts is not None:
        for item in facts:
            if item.get("relation_type") not in {k.value for k in M.RelationType}:
                return M.bad_request(
                    f"bad relation_type {item.get('relation_type')!r}; "
                    f"expected one of {[k.value for k in M.RelationType]}"
                )
            err = _require_evidence_architecture(db, arch_id, item.get("fact_id"))
            if err is not None:
                return err

    if result is None and rationale is None and confidence is None and facts is None:
        return M.bad_request("no fields to update")

    # --- atomic write ---------------------------------------------------------
    try:
        with db.transaction() as conn:
            sets: list[str] = []
            vals: list[object] = []
            if result is not None:
                sets.append("result = ?")
                vals.append(result)
            if rationale is not None:
                sets.append("rationale = ?")
                vals.append(rationale)
            if confidence is not None:
                sets.append("confidence = ?")
                vals.append(confidence)
            if sets:
                vals.append(assessment_id)
                conn.execute(f"UPDATE assessments SET {', '.join(sets)} WHERE id = ?", vals)

            if facts is not None:
                conn.execute(
                    "DELETE FROM assessment_facts WHERE assessment_id = ?",
                    (assessment_id,),
                )
                for item in facts:
                    conn.execute(
                        "INSERT INTO assessment_facts (assessment_id, fact_id, relation_type) "
                        "VALUES (?, ?, ?)",
                        (assessment_id, item["fact_id"], item["relation_type"]),
                    )
    except sqlite3.IntegrityError as exc:
        msg = str(exc)
        if "UNIQUE" in msg:
            return M.conflict("evidence fact already attached")
        if "FOREIGN KEY" in msg:
            return M.not_found("fact", id=None)
        return M.conflict(msg)

    row = db.connection.execute(
        "SELECT * FROM assessments WHERE id = ?", (assessment_id,)
    ).fetchone()
    return M.ok(**dict(row))


def assessment_evidence(db: Database, assessment_id: int) -> dict:
    """List the evidence facts of an assessment, grouped by relation type."""
    if not db.connection.execute(
        "SELECT 1 FROM assessments WHERE id = ?", (assessment_id,)
    ).fetchone():
        return M.not_found("assessment", id=assessment_id)
    rows = db.connection.execute(
        "SELECT af.fact_id, af.relation_type, f.fact_text, f.architecture_id "
        "FROM assessment_facts af JOIN facts f ON f.id = af.fact_id "
        "WHERE af.assessment_id = ? "
        "ORDER BY CASE af.relation_type "
        "         WHEN 'SUPPORTS' THEN 1 WHEN 'CONTRADICTS' THEN 2 ELSE 3 END, "
        "         af.fact_id",
        (assessment_id,),
    ).fetchall()
    return M.ok(items=[dict(r) for r in rows])


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def register(mcp, db: Database) -> None:
    """Expose assessment tools on the MCP server."""

    @mcp.tool()
    def assessment_create(
        requirement_id: int,
        architecture_id: int,
        result: str,
        rationale: str,
        confidence: float = 1.0,
        facts: list[dict] | None = None,
    ) -> dict:
        """Create a compliance assessment for one requirement vs one architecture.

        `facts` optionally lists initial evidence:
        [{"fact_id": 1, "relation_type": "SUPPORTS" | "CONTRADICTS" | "CONTEXT"}].
        Result must be one of COMPLIANT/INCOMPLIANT/PARTIAL/NOT_APPLICABLE/INSUFFICIENT_DATA.
        """
        return create_assessment(
            db, requirement_id=requirement_id, architecture_id=architecture_id,
            result=result, rationale=rationale, confidence=confidence, facts=facts,
        )

    @mcp.tool()
    def assessment_get(assessment_id: int) -> dict:
        """Fetch an assessment by id."""
        return get_assessment(db, assessment_id)

    @mcp.tool()
    def assessment_list(
        requirement_id: int | None = None,
        architecture_id: int | None = None,
        result: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """List assessments, optionally filtered by requirement/architecture/result."""
        return list_assessments(
            db, requirement_id=requirement_id, architecture_id=architecture_id,
            result=result, limit=limit, offset=offset,
        )

    @mcp.tool()
    def assessment_attach_fact(
        assessment_id: int,
        fact_id: int,
        relation_type: str,
    ) -> dict:
        """Attach (or re-classify) a fact as evidence for an assessment.

        relation_type: SUPPORTS | CONTRADICTS | CONTEXT."""
        return attach_fact(db, assessment_id=assessment_id,
                           fact_id=fact_id, relation_type=relation_type)

    @mcp.tool()
    def assessment_detach_fact(assessment_id: int, fact_id: int) -> dict:
        """Remove a fact from the evidence of an assessment."""
        return detach_fact(db, assessment_id=assessment_id, fact_id=fact_id)

    @mcp.tool()
    def assessment_update(
        assessment_id: int,
        result: str | None = None,
        rationale: str | None = None,
        confidence: float | None = None,
        facts: list[dict] | None = None,
    ) -> dict:
        """Re-assess an existing assessment (ADR-001 §5).

        Update result / rationale / confidence when provided. If `facts` is
        provided, it REPLACES the whole evidence set atomically:
        [{"fact_id": 1, "relation_type": "SUPPORTS" | "CONTRADICTS" | "CONTEXT"}].
        Facts must belong to the assessment's architecture. If no field is
        provided, the call is rejected.
        """
        return update_assessment(
            db, assessment_id=assessment_id, result=result,
            rationale=rationale, confidence=confidence, facts=facts,
        )

    @mcp.tool()
    def assessment_facts(assessment_id: int) -> dict:
        """List all evidence facts of an assessment with their relation types."""
        return assessment_evidence(db, assessment_id)
