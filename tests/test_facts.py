"""Tests for architecture_mcp.facts domain ops and MCP registration."""
from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver import MCPServer

from architecture_mcp import facts as F
from architecture_mcp import sources as S

_TOOL_NAMES = {
    "fact_create",
    "fact_get",
    "fact_list",
    "fact_search",
    "fact_update",
    "fact_categories",
    "fact_correct_quote",
}


@pytest.fixture
def arch(db) -> int:
    r = S.create_architecture(db, name="Референс-архитектура", version="1.0")
    assert r["ok"], r
    return r["id"]


@pytest.fixture
def src(db) -> int:
    r = S.create_source(db, source_type="ARCH", title="АД (архитектурный документ)")
    assert r["ok"], r
    return r["id"]


def test_create_fact_round_trip(db, arch, src) -> None:
    r = F.create_fact(
        db, architecture_id=arch, source_id=src,
        fact_text="Капитализация на базе Python 3.12",
        source_locator="раздел 2", source_quote="стек — Python",
    )
    assert r["ok"] is True
    assert r["architecture_id"] == arch
    assert r["source_id"] == src
    got = F.get_fact(db, r["id"])
    assert got["ok"] is True
    assert got["fact_text"] == "Капитализация на базе Python 3.12"


def test_create_fact_rejects_missing_architecture(db, src) -> None:
    r = F.create_fact(db, architecture_id=9999, source_id=src, fact_text="x")
    assert r["ok"] is False
    assert r["code"] == "NOT_FOUND"
    assert "architecture" in r["error"]


def test_create_fact_rejects_missing_source(db, arch) -> None:
    r = F.create_fact(db, architecture_id=arch, source_id=9999, fact_text="x")
    assert r["ok"] is False
    assert r["code"] == "NOT_FOUND"
    assert "source" in r["error"]


def test_create_fact_rejects_empty_text(db, arch, src) -> None:
    r = F.create_fact(db, architecture_id=arch, source_id=src, fact_text="  ")
    assert r["ok"] is False
    assert r["code"] == "BAD_REQUEST"


def test_fact_get_missing(db) -> None:
    r = F.get_fact(db, 424242)
    assert r["ok"] is False
    assert r["code"] == "NOT_FOUND"


def test_list_filtered_by_architecture(db, arch, src) -> None:
    other_arch = S.create_architecture(db, name="Другая")["id"]
    for i in range(3):
        F.create_fact(db, architecture_id=arch, source_id=src, fact_text=f"факт-{i}")
    F.create_fact(db, architecture_id=other_arch, source_id=src, fact_text="чужой")
    page = F.list_facts(db, architecture_id=arch)
    assert page["ok"] is True
    assert len(page["items"]) == 3
    assert all(i["architecture_id"] == arch for i in page["items"])


def test_search_isolated_by_architecture(db, src) -> None:
    """ADR-001 §3: search over architecture A must not leak facts of B,
    even when the SAME text substring matches both. Regression guard
    against the classic `A OR B AND X` mis-binding."""
    r_a = S.create_architecture(db, name="Architecture A", version="1")
    r_b = S.create_architecture(db, name="Architecture B", version="1")
    a_id, b_id = r_a["id"], r_b["id"]

    F.create_fact(db, architecture_id=a_id, source_id=src,
                  fact_text="Database is PostgreSQL on AWS")
    # Identical text, different architecture.
    F.create_fact(db, architecture_id=b_id, source_id=src,
                  fact_text="Database is PostgreSQL on Azure")

    res = F.search_facts(db, query="PostgreSQL", architecture_id=a_id)
    assert res["ok"] is True
    items = res["items"]
    assert len(items) == 1, items
    assert items[0]["architecture_id"] == a_id

    # Control: same query with no filter returns both.
    no_arch = F.search_facts(db, query="PostgreSQL")
    assert len(no_arch["items"]) == 2

    # And architecture B filter returns only B.
    res_b = F.search_facts(db, query="PostgreSQL", architecture_id=b_id)
    assert len(res_b["items"]) == 1
    assert res_b["items"][0]["architecture_id"] == b_id


def test_search_and_escaping(db, arch, src) -> None:
    F.create_fact(db, architecture_id=arch, source_id=src, fact_text="Uses 80% CPU")
    F.create_fact(db, architecture_id=arch, source_id=src, fact_text="Uses little CPU")
    res = F.search_facts(db, query="80%")
    assert res["ok"] is True
    assert len(res["items"]) == 1
    assert res["items"][0]["fact_text"] == "Uses 80% CPU"

    res2 = F.search_facts(db, query="USES", architecture_id=arch)
    assert len(res2["items"]) == 2


def test_facts_update_rejects_source_quote(db, arch, src) -> None:
    """ADR-001 §7/§9.7: fact_update must refuse to change source_quote."""
    fid = F.create_fact(
        db, architecture_id=arch, source_id=src, fact_text="t", source_quote="orig"
    )["id"]
    r = F.update_fact(db, fid, fact_text="t2", source_quote="hacked")
    assert r["ok"] is False
    assert r["code"] == "BAD_REQUEST", r
    got = F.get_fact(db, fid)
    assert got["source_quote"] == "orig"
    assert got["fact_text"] == "t"


def test_correct_fact_quote_dedicated(db, arch, src) -> None:
    fid = F.create_fact(
        db, architecture_id=arch, source_id=src, fact_text="t", source_quote="wrong"
    )["id"]
    r = F.correct_fact_quote(db, fid, source_quote="the true quote")
    assert r["ok"] is True, r
    assert r["source_quote"] == "the true quote"
    assert F.correct_fact_quote(db, fid, source_quote="")["code"] == "BAD_REQUEST"
    assert F.correct_fact_quote(db, 999, source_quote="x")["code"] == "NOT_FOUND"


def test_update_partial_fields(db, arch, src) -> None:
    fid = F.create_fact(db, architecture_id=arch, source_id=src, fact_text="old")["id"]
    r = F.update_fact(db, fid, status="SUPERSEDED")
    assert r["ok"] is True
    assert r["status"] == "SUPERSEDED"
    assert r["fact_text"] == "old"

    assert F.update_fact(db, fid)["code"] == "BAD_REQUEST"
    assert F.update_fact(db, 999, fact_text="x")["code"] == "NOT_FOUND"
    assert F.update_fact(db, fid, fact_text="")["code"] == "BAD_REQUEST"


def test_fact_categories_before_assignment(db, arch, src) -> None:
    fid = F.create_fact(db, architecture_id=arch, source_id=src, fact_text="x")["id"]
    res = F.list_fact_categories(db, fid)
    assert res["ok"] is True
    assert res["items"] == []


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_exposes_expected_tools(db, arch, src) -> None:
    mcp = MCPServer("test")
    F.register(mcp, db)
    names = {t.name for t in await mcp.list_tools()}
    missing = _TOOL_NAMES - names
    assert missing == set(), f"missing tools: {sorted(missing)}"


@pytest.mark.asyncio
async def test_create_via_mcp_tool(db, arch, src) -> None:
    mcp = MCPServer("test")
    F.register(mcp, db)
    res = await mcp.call_tool(
        "fact_create",
        {"architecture_id": arch, "source_id": src, "fact_text": "через MCP"},
    )
    payload = json.loads(res.content[0].text)
    assert payload["ok"] is True
    assert payload["fact_text"] == "через MCP"
