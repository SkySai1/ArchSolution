"""Tests for architecture_mcp.assessments domain ops."""
from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver import MCPServer

from architecture_mcp import assessments as A
from architecture_mcp import facts as F
from architecture_mcp import requirements as R
from architecture_mcp import sources as S

_TOOL_NAMES = {
    "assessment_create",
    "assessment_get",
    "assessment_list",
    "assessment_attach_fact",
    "assessment_detach_fact",
    "assessment_facts",
}


@pytest.fixture
def seed(db):
    src = S.create_source(db, source_type="TD", title="ТЗ")["id"]
    arch = S.create_architecture(db, name="Целевая", version="1.0")["id"]
    rid = R.create_requirement(
        db, source_id=src, requirement_text="Система хранит данные в SQLite"
    )["id"]
    f1 = F.create_fact(
        db, architecture_id=arch, source_id=src,
        fact_text="Хранилище — локальная SQLite",
    )["id"]
    f2 = F.create_fact(
        db, architecture_id=arch, source_id=src, fact_text="Кэш — Redis"
    )["id"]
    return {"src": src, "arch": arch, "rid": rid, "f1": f1, "f2": f2}


def test_create_with_facts_in_one_tx(db, seed) -> None:
    r = A.create_assessment(
        db, requirement_id=seed["rid"], architecture_id=seed["arch"],
        result="COMPLIANT", rationale="факт F1 прямо подтверждает",
        confidence=0.9,
        facts=[
            {"fact_id": seed["f1"], "relation_type": "SUPPORTS"},
            {"fact_id": seed["f2"], "relation_type": "CONTEXT"},
        ],
    )
    assert r["ok"] is True, r
    assert r["result"] == "COMPLIANT"
    ev = A.assessment_evidence(db, r["id"])["items"]
    assert len(ev) == 2
    assert {i["relation_type"] for i in ev} == {"SUPPORTS", "CONTEXT"}


def test_create_insufficient_data(db, seed) -> None:
    r = A.create_assessment(
        db, requirement_id=seed["rid"], architecture_id=seed["arch"],
        result="INSUFFICIENT_DATA", rationale="нет фактов о СУБД",
    )
    assert r["ok"] is True
    assert r["result"] == "INSUFFICIENT_DATA"


def test_duplicate_pair_is_conflict(db, seed) -> None:
    args = dict(requirement_id=seed["rid"], architecture_id=seed["arch"],
                rationale="r")
    assert A.create_assessment(db, result="COMPLIANT", **args)["ok"] is True
    r2 = A.create_assessment(db, result="INCOMPLIANT", **args)
    assert r2["ok"] is False
    assert r2["code"] == "CONFLICT"


def test_bad_inputs(db, seed) -> None:
    rid, arch = seed["rid"], seed["arch"]
    assert A.create_assessment(db, requirement_id=rid, architecture_id=arch,
                               result="NOPE", rationale="r")["code"] in ("BAD_REQUEST",)
    assert A.create_assessment(db, requirement_id=rid, architecture_id=arch,
                               result="COMPLIANT", rationale="  ")[
        "code"] == "BAD_REQUEST"
    assert A.create_assessment(db, requirement_id=rid, architecture_id=arch,
                               result="COMPLIANT", rationale="r",
                               confidence=2)["code"] == "BAD_REQUEST"
    assert A.create_assessment(db, requirement_id=999, architecture_id=arch,
                               result="COMPLIANT", rationale="r")["code"] == "NOT_FOUND"
    assert A.create_assessment(db, requirement_id=rid, architecture_id=999,
                               result="COMPLIANT", rationale="r")["code"] == "NOT_FOUND"
    assert A.create_assessment(
        db, requirement_id=rid, architecture_id=arch,
        result="COMPLIANT", rationale="r",
        facts=[{"fact_id": seed["f1"], "relation_type": "WRONG"}],
    )["code"] == "BAD_REQUEST"
    assert A.create_assessment(
        db, requirement_id=rid, architecture_id=arch,
        result="COMPLIANT", rationale="r",
        facts=[{"fact_id": 999, "relation_type": "SUPPORTS"}],
    )["code"] == "NOT_FOUND"


def test_attach_detach_fact(db, seed) -> None:
    aid = A.create_assessment(
        db, requirement_id=seed["rid"], architecture_id=seed["arch"],
        result="PARTIAL", rationale="partly",
    )["id"]
    a = A.attach_fact(
        db, assessment_id=aid, fact_id=seed["f1"], relation_type="SUPPORTS"
    )
    assert a["ok"] is True and a["fact_text"]
    # re-attach changes relation type (upsert)
    assert A.attach_fact(
        db, assessment_id=aid, fact_id=seed["f1"], relation_type="CONTRADICTS"
    )["relation_type"] == "CONTRADICTS"
    assert len(A.assessment_evidence(db, aid)["items"]) == 1
    d = A.detach_fact(db, assessment_id=aid, fact_id=seed["f1"])
    assert d["ok"] is True and d["deleted"] is True
    assert A.assessment_evidence(db, aid)["items"] == []
    assert A.detach_fact(db, assessment_id=aid, fact_id=seed["f1"])["code"] == "NOT_FOUND"
    assert A.attach_fact(
        db, assessment_id=aid, fact_id=999, relation_type="SUPPORTS"
    )["code"] == "NOT_FOUND"
    assert A.attach_fact(
        db, assessment_id=999, fact_id=seed["f1"], relation_type="SUPPORTS"
    )["code"] == "NOT_FOUND"


def test_list_filters(db, seed) -> None:
    arch2 = S.create_architecture(db, name="Вторая")["id"]
    A.create_assessment(db, requirement_id=seed["rid"], architecture_id=seed["arch"],
                        result="COMPLIANT", rationale="r1")
    A.create_assessment(db, requirement_id=seed["rid"], architecture_id=arch2,
                        result="INCOMPLIANT", rationale="r2")
    res = A.list_assessments(db, architecture_id=arch2)
    assert res["ok"] is True
    assert len(res["items"]) == 1
    assert res["items"][0]["result"] == "INCOMPLIANT"
    assert A.list_assessments(db, result="NOPE")["code"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_register_exposes_expected_tools(db, seed) -> None:
    mcp = MCPServer("test")
    A.register(mcp, db)
    names = {t.name for t in await mcp.list_tools()}
    missing = _TOOL_NAMES - names
    assert missing == set(), f"missing tools: {sorted(missing)}"


@pytest.mark.asyncio
async def test_create_via_mcp_tool(db, seed) -> None:
    mcp = MCPServer("test")
    A.register(mcp, db)
    res = await mcp.call_tool(
        "assessment_create",
        {
            "requirement_id": seed["rid"],
            "architecture_id": seed["arch"],
            "result": "INSUFFICIENT_DATA",
            "rationale": "через MCP",
            "facts": [{"fact_id": seed["f1"], "relation_type": "CONTEXT"}],
        },
    )
    payload = json.loads(res.content[0].text)
    assert payload["ok"] is True
    assert payload["result"] == "INSUFFICIENT_DATA"
