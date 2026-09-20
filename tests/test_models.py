"""Tests for shared enums and helpers in architecture_mcp.models."""
from __future__ import annotations

import enum

from architecture_mcp import models as M


def test_source_kind_members() -> None:
    assert {k.value for k in M.SourceKind} == {
        "NPA", "TD", "ARCH", "STANDARD", "INTERNAL",
    }


def test_npa_status_members() -> None:
    assert {k.value for k in M.NpaStatus} == {
        "ACTIVE", "SUPERSEDED", "REPEALED", "DRAFT",
    }


def test_assessment_result_includes_insufficient_data() -> None:
    values = {k.value for k in M.AssessmentResult}
    assert "INSUFFICIENT_DATA" in values
    assert values == {
        "COMPLIANT", "INCOMPLIANT", "PARTIAL", "UNKNOWN", "INSUFFICIENT_DATA",
    }


def test_relation_type_members() -> None:
    assert {k.value for k in M.RelationType} == {"SUPPORTS", "CONTRADICTS", "CONTEXT"}


def test_category_scope_members() -> None:
    assert {k.value for k in M.CategoryScope} == {
        "REQUIREMENT", "FACT", "BOTH", "ASSESSMENT",
    }


def test_enums_are_string_backed() -> None:
    # Membership against raw strings must work (str-enum).
    for cls, literal in [
        (M.SourceKind, "NPA"),
        (M.AssessmentResult, "INSUFFICIENT_DATA"),
        (M.RelationType, "SUPPORTS"),
    ]:
        value = cls(literal)
        assert isinstance(value, enum.Enum)
        assert value.value == literal


def test_ok_envelope() -> None:
    r = M.ok(id=42)
    assert r["ok"] is True
    assert r["id"] == 42


def test_err_envelope_default_code() -> None:
    r = M.err("boom")
    assert r["ok"] is False
    assert r["error"] == "boom"
    assert r["code"] == "ERROR"


def test_err_envelope_custom_code() -> None:
    r = M.err("boom", code="SOME_CODE")
    assert r["code"] == "SOME_CODE"
    assert "SOME_CODE" not in r  # code is not duplicated as a payload key


def test_not_found_payload() -> None:
    r = M.not_found("requirement", id=7)
    assert r["ok"] is False
    assert r["code"] == "NOT_FOUND"
    assert r["id"] == 7
    assert "requirement" in r["error"]


def test_bad_request_and_conflict() -> None:
    assert M.bad_request("missing title")["code"] == "BAD_REQUEST"
    assert M.conflict("duplicated name")["code"] == "CONFLICT"


def test_limits_are_positive() -> None:
    assert M.DEFAULT_LIMIT > 0
    assert M.MAX_LIMIT > M.DEFAULT_LIMIT
