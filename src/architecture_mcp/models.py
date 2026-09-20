"""Shared enums, limits, and result helpers for architecture_mcp domain modules."""
from __future__ import annotations

import enum

# ---------------------------------------------------------------------------
# Hard limits
# ---------------------------------------------------------------------------
#: Default page size for list endpoints.
DEFAULT_LIMIT = 50
#: Maximum page size accepted by any list endpoint.
MAX_LIMIT = 500


# ---------------------------------------------------------------------------
# Source types
# ---------------------------------------------------------------------------
class SourceKind(str, enum.Enum):
    """Kind of a source document stored in `sources`."""

    NPA = "NPA"              # нормативно-правовой акт
    TD = "TD"                # техническое задание / ТЗ
    ARCH = "ARCH"            # архитектурная документация (ADR / architecture doc)
    STANDARD = "STANDARD"    # отраслевой / межотраслевой стандарт
    INTERNAL = "INTERNAL"    # внутренний документ


class NpaStatus(str, enum.Enum):
    """Legal status of an НПА source (only used when `source_type == NPA`)."""

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REPEALED = "REPEALED"
    DRAFT = "DRAFT"


class SourceStatus(str, enum.Enum):
    """Lifecycle status of any source row."""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ArchitectureStatus(str, enum.Enum):
    """Lifecycle status of an analyzed architecture."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class RequirementStatus(str, enum.Enum):
    """Lifecycle status of an individual requirement."""

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class FactStatus(str, enum.Enum):
    """Lifecycle status of an individual architectural fact."""

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class CategoryScope(str, enum.Enum):
    """Which side(s) of the domain a category is relevant to.

    A category applies to requirements, to facts, to both, or to assessments
    (a meta-category used to group assessments, not individual items)."""

    REQUIREMENT = "REQUIREMENT"
    FACT = "FACT"
    BOTH = "BOTH"
    ASSESSMENT = "ASSESSMENT"


class CategoryStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------
class AssessmentResult(str, enum.Enum):
    """Compliance verdict of a single (requirement, architecture) pair.

    ADR-001 §6: `UNKNOWN` removed — it was used as a synonym of
    `INSUFFICIENT_DATA`. `NOT_APPLICABLE` is the explicit "requirement
    was considered but does not apply to this architecture" state.
    """

    COMPLIANT = "COMPLIANT"
    INCOMPLIANT = "INCOMPLIANT"
    PARTIAL = "PARTIAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class RelationType(str, enum.Enum):
    """How a referenced fact contributes to an assessment."""

    SUPPORTS = "SUPPORTS"        # факт подтверждает соответствие
    CONTRADICTS = "CONTRADICTS"  # факт противоречит требованию
    CONTEXT = "CONTEXT"          # даёт дополнительный контекст


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------
def ok(**payload: object) -> dict:
    """Build a standard success envelope."""
    out: dict = {"ok": True}
    out.update(payload)
    return out


def err(message: str, **payload: object) -> dict:
    """Build a standard error envelope.

    `code` is a stable, machine-readable identifier (e.g. `NOT_FOUND`).
    """
    code = payload.pop("code", "ERROR")
    out: dict = {"ok": False, "error": message, "code": code}
    out.update(payload)
    return out


def not_found(resource: str, **identifiers: object) -> dict:
    """Standard 404-like payload."""
    return err(f"{resource} not found", code="NOT_FOUND", **identifiers)


def bad_request(message: str, **payload: object) -> dict:
    """Standard 400-like payload."""
    return err(message, code="BAD_REQUEST", **payload)


def conflict(message: str, **payload: object) -> dict:
    """Standard 409-like payload."""
    return err(message, code="CONFLICT", **payload)
