"""Tests for architecture_mcp.sources domain ops and MCP registration."""
from __future__ import annotations

from architecture_mcp import models as M
from architecture_mcp import sources as S


def test_create_source_round_trip(db) -> None:
    r = S.create_source(db, source_type="TD", title="ТЗ v1")
    assert r["ok"] is True
    sid = r["id"]
    assert r["source_type"] == "TD"
    assert r["status"] == "ACTIVE"
    got = S.get_source(db, sid)
    assert got["ok"] is True
    assert got["title"] == "ТЗ v1"


def test_create_source_rejects_bad_kind(db) -> None:
    r = S.create_source(db, source_type="NOT_A_KIND", title="x")
    assert r["ok"] is False
    assert r["code"] == "BAD_REQUEST"


def test_create_source_rejects_empty_title(db) -> None:
    r = S.create_source(db, source_type="TD", title="   ")
    assert r["ok"] is False
    assert r["code"] == "BAD_REQUEST"


def test_source_get_missing(db) -> None:
    r = S.get_source(db, 99999)
    assert r["ok"] is False
    assert r["code"] == "NOT_FOUND"


def test_source_list_has_limit_offset(db) -> None:
    for i in range(7):
        S.create_source(db, source_type="TD", title=f"t{i}")
    page = S.list_sources(db, limit=3, offset=0)
    assert page["ok"] is True
    assert page["limit"] == 3
    assert page["offset"] == 0
    assert len(page["items"]) == 3


def test_source_list_clamps_limits(db) -> None:
    page = S.list_sources(db, limit=10_000)
    assert page["limit"] == M.MAX_LIMIT


def test_npa_attach_on_npa_source(db) -> None:
    src = S.create_source(db, source_type="NPA", title="Постановление")
    assert src["ok"]
    r = S.attach_npa(
        db, source_id=src["id"], document_type="Постановление",
        number="п-1234", issuer="Росстандарт",
    )
    assert r["ok"] is True
    assert r["source_id"] == src["id"]
    assert r["number"] == "п-1234"
    got = S.get_npa(db, src["id"])
    assert got["ok"] is True
    assert got["issuer"] == "Росстандарт"


def test_npa_attach_replaces(db) -> None:
    src = S.create_source(db, source_type="NPA", title="X")
    S.attach_npa(db, source_id=src["id"], document_type="Постановление", number="v1")
    S.attach_npa(db, source_id=src["id"], document_type="Приказ", number="v2")
    got = S.get_npa(db, src["id"])
    assert got["document_type"] == "Приказ"
    assert got["number"] == "v2"


def test_npa_attach_rejected_for_non_npa(db) -> None:
    src = S.create_source(db, source_type="TD", title="ТЗ")
    r = S.attach_npa(db, source_id=src["id"], document_type="Постановление")
    assert r["ok"] is False
    assert r["code"] == "BAD_REQUEST"
    assert "НПА" in r["error"] or "NPA" in r["error"]


def test_architecture_create_and_get(db) -> None:
    r = S.create_architecture(db, name="Monolith", version="1.0")
    assert r["ok"] is True
    got = S.get_architecture(db, r["id"])
    assert got["ok"] is True
    assert got["name"] == "Monolith"


def test_architecture_name_unique_constraint(db) -> None:
    S.create_architecture(db, name="Monolith", version="1.0")
    r = S.create_architecture(db, name="Monolith", version="1.0")
    assert r["ok"] is False
    assert r["code"] == "CONFLICT"


def test_architecture_list_limit(db) -> None:
    for i in range(5):
        S.create_architecture(db, name=f"A{i}", version="1")
    page = S.list_architectures(db, limit=2, offset=0)
    assert len(page["items"]) == 2


