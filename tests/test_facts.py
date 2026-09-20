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


def test_search_and_escaping(db, arch, src) -> None:
    F.create_fact(db, architecture_id=arch, source_id=src, fact_text="Uses 80% CPU")
    F.create_fact(db, architecture_id=arch, source_id=src, fact_text="Uses little CPU")
    res = F.search_facts(db, query="80%")
    assert res["ok"] is True
    assert len(res["items"]) == 1
    assert res["items"][0]["fact_text"] == "Uses 80% CPU"

    res2 = F.search_facts(db, query="USES", architecture_id=arch)
    assert len(res2["items"]) == 2


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
