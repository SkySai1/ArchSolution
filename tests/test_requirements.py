"""Tests for architecture_mcp.requirements domain ops and MCP registration."""
from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver import MCPServer

from architecture_mcp import models as M
from architecture_mcp import requirements as R
from architecture_mcp import sources as S

_TOOL_NAMES = {
    "requirement_create",
    "requirement_get",
    "requirement_list",
    "requirement_search",
    "requirement_update",
    "requirement_categories",
    "requirement_correct_quote",
}


@pytest.fixture
def src(db) -> int:
    r = S.create_source(db, source_type="TD", title="ТЗ для требований")
    assert r["ok"], r
    return r["id"]


def test_create_requirement_round_trip(db, src) -> None:
    r = R.create_requirement(
        db, source_id=src, requirement_text="Данные хранятся в SQLite",
        source_locator="п. 3.2", source_quote="БД — локальная SQLite",
    )
    assert r["ok"] is True
    assert r["source_id"] == src
    assert r["source_locator"] == "п. 3.2"
    got = R.get_requirement(db, r["id"])
    assert got["ok"] is True
    assert got["requirement_text"] == "Данные хранятся в SQLite"


def test_create_requirement_rejects_missing_source(db) -> None:
    r = R.create_requirement(db, source_id=9999, requirement_text="x")
    assert r["ok"] is False
    assert r["code"] == "NOT_FOUND"


def test_create_requirement_rejects_empty_text(db, src) -> None:
    r = R.create_requirement(db, source_id=src, requirement_text="   ")
    assert r["ok"] is False
    assert r["code"] == "BAD_REQUEST"


def test_requirement_get_missing(db) -> None:
    r = R.get_requirement(db, 424242)
    assert r["ok"] is False
    assert r["code"] == "NOT_FOUND"


def test_list_filtered_by_source_and_paged(db, src) -> None:
    other = S.create_source(db, source_type="TD", title="Другое ТЗ")["id"]
    for i in range(4):
        R.create_requirement(db, source_id=src, requirement_text=f"треб-А-{i}")
    R.create_requirement(db, source_id=other, requirement_text="треб-Б")

    page = R.list_requirements(db, source_id=src, limit=2, offset=1)
    assert page["ok"] is True
    assert len(page["items"]) == 2
    assert all(item["source_id"] == src for item in page["items"])


def test_search_case_insensitive(db, src) -> None:
    R.create_requirement(db, source_id=src, requirement_text="System must Log ALL errors")
    R.create_requirement(db, source_id=src, requirement_text="Cache responses")
    res = R.search_requirements(db, query="log all")
    assert res["ok"] is True
    assert len(res["items"]) == 1
    assert "Log" in res["items"][0]["requirement_text"]


def test_search_escapes_like_wildcards(db, src) -> None:
    R.create_requirement(db, source_id=src, requirement_text="match 100% coverage")
    R.create_requirement(db, source_id=src, requirement_text="match any value")
    res = R.search_requirements(db, query="100%")
    assert res["ok"] is True
    assert len(res["items"]) == 1
    assert res["items"][0]["requirement_text"] == "match 100% coverage"


def test_update_partial_fields(db, src) -> None:
    rid = R.create_requirement(
        db, source_id=src, requirement_text="first", source_quote="q"
    )["id"]
    r = R.update_requirement(db, rid, status=M.RequirementStatus.SUPERSEDED.value)
    assert r["ok"] is True
    assert r["status"] == "SUPERSEDED"
    assert r["requirement_text"] == "first"  # not touched


def test_update_rejects_source_quote(db, src) -> None:
    """ADR-001 §7/§9.7: the generic update must refuse to change source_quote."""
    rid = R.create_requirement(
        db, source_id=src, requirement_text="t", source_quote="orig quote"
    )["id"]
    r = R.update_requirement(db, rid, requirement_text="t2", source_quote="hacked")
    assert r["ok"] is False
    assert r["code"] == "BAD_REQUEST", r
    # And the quote (and text) are untouched on rejection.
    got = R.get_requirement(db, rid)
    assert got["source_quote"] == "orig quote"
    assert got["requirement_text"] == "t"


def test_correct_requirement_quote_dedicated(db, src) -> None:
    """ADR-001 §7.3: a wrongly-recorded quote is fixed via the dedicated tool."""
    rid = R.create_requirement(
        db, source_id=src, requirement_text="t", source_quote="wrong"
    )["id"]
    r = R.correct_requirement_quote(db, rid, source_quote="the true quote")
    assert r["ok"] is True, r
    assert r["source_quote"] == "the true quote"
    # Guards.
    assert R.correct_requirement_quote(db, rid, source_quote="")["code"] == "BAD_REQUEST"
    assert R.correct_requirement_quote(db, 999, source_quote="x")["code"] == "NOT_FOUND"


def test_update_rejects_empty_text_and_no_fields(db, src) -> None:
    rid = R.create_requirement(db, source_id=src, requirement_text="x")["id"]
    assert R.update_requirement(db, rid, requirement_text="")["code"] == "BAD_REQUEST"
    assert R.update_requirement(db, rid)["code"] == "BAD_REQUEST"


def test_update_missing_requirement(db) -> None:
    r = R.update_requirement(db, 999, status="SUPERSEDED")
    assert r["ok"] is False
    assert r["code"] == "NOT_FOUND"


def test_requirement_categories_before_assignment(db, src) -> None:
    rid = R.create_requirement(db, source_id=src, requirement_text="x")["id"]
    res = R.list_requirement_categories(db, rid)
    assert res["ok"] is True
    assert res["items"] == []


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_exposes_expected_tools(db, src) -> None:
    mcp = MCPServer("test")
    R.register(mcp, db)
    names = {t.name for t in await mcp.list_tools()}
    missing = _TOOL_NAMES - names
    assert missing == set(), f"missing tools: {sorted(missing)}"


@pytest.mark.asyncio
async def test_create_via_mcp_tool(db, src) -> None:
    mcp = MCPServer("test")
    R.register(mcp, db)
    res = await mcp.call_tool(
        "requirement_create",
        {"source_id": src, "requirement_text": "через MCP"},
    )
    payload = json.loads(res.content[0].text)
    assert payload["ok"] is True
    assert payload["requirement_text"] == "через MCP"
    assert any(i["requirement_text"] == "через MCP"
               for i in R.list_requirements(db)["items"])
