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
    "assessment_update",
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


def test_not_applicable_accepted(db, seed) -> None:
    """ADR-001 §6.2/§9.6: NOT_APPLICABLE is a first-class result meaning
    the requirement was considered but does not apply to this architecture."""
    arch2 = S.create_architecture(db, name="Применение вне темы", version="1.0")["id"]
    r = A.create_assessment(
        db, requirement_id=seed["rid"], architecture_id=arch2,
        result="NOT_APPLICABLE",
        rationale="архитектура не содержит удалённого доступа — требование MFA неприменимо",
    )
    assert r["ok"] is True, r
    assert r["result"] == "NOT_APPLICABLE"
    listed = A.list_assessments(db, architecture_id=arch2, result="NOT_APPLICABLE")
    assert listed["ok"] is True
    assert len(listed["items"]) == 1


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


def test_create_rejects_foreign_architecture_fact(db, seed) -> None:
    """ADR-001 §4/§9.2: assessment_create with a fact belonging to another
    architecture must be rejected as BAD_REQUEST and nothing partially written.
    """
    arch2 = S.create_architecture(db, name="Чужая арк", version="1.0")["id"]
    foreign = F.create_fact(
        db, architecture_id=arch2, source_id=seed["src"], fact_text="чужой факт"
    )["id"]
    # No facts existed in the first place; create is expected to fail cleanly.
    before_asmt = db.connection.execute("SELECT count(*) AS c FROM assessments").fetchone()["c"]
    r = A.create_assessment(
        db, requirement_id=seed["rid"], architecture_id=seed["arch"],
        result="COMPLIANT", rationale="r",
        facts=[{"fact_id": foreign, "relation_type": "SUPPORTS"}],
    )
    assert r["ok"] is False
    assert r["code"] == "BAD_REQUEST", r
    assert "architecture" in r["error"]
    after_asmt = db.connection.execute("SELECT count(*) AS c FROM assessments").fetchone()["c"]
    assert before_asmt == after_asmt          # nothing written
    assert db.connection.execute("SELECT count(*) AS c FROM assessment_facts").fetchone()["c"] == 0
    # Second fact from the same architecture is still accepted.
    ok = A.create_assessment(
        db, requirement_id=seed["rid"], architecture_id=seed["arch"],
        result="COMPLIANT", rationale="r",
        facts=[{"fact_id": seed["f1"], "relation_type": "SUPPORTS"}],
    )
    assert ok["ok"] is True, ok


def test_attach_fact_rejects_foreign_architecture(db, seed) -> None:
    """ADR-001 §4/§9.3: attach_fact with a fact from another architecture
    must refuse without altering existing evidence."""
    # First create an assessment with the architecture's own evidence.
    aid = A.create_assessment(
        db, requirement_id=seed["rid"], architecture_id=seed["arch"],
        result="PARTIAL", rationale="partly",
        facts=[{"fact_id": seed["f1"], "relation_type": "SUPPORTS"}],
    )["id"]
    # Now attach a foreign fact — must fail.
    arch2 = S.create_architecture(db, name="Чужая арк 2", version="1.0")["id"]
    foreign = F.create_fact(
        db, architecture_id=arch2, source_id=seed["src"], fact_text="чужой факт 2"
    )["id"]
    r = A.attach_fact(db, assessment_id=aid, fact_id=foreign, relation_type="SUPPORTS")
    assert r["ok"] is False
    assert r["code"] == "BAD_REQUEST", r
    # Original evidence untouched.
    ev = A.assessment_evidence(db, aid)["items"]
    assert len(ev) == 1 and ev[0]["fact_id"] == seed["f1"]
    assert ev[0]["relation_type"] == "SUPPORTS"


def test_update_reassessment_fields(db, seed) -> None:
    """ADR-001 §5/§9.4: reassess changes result/rationale/confidence atomically."""
    aid = A.create_assessment(
        db, requirement_id=seed["rid"], architecture_id=seed["arch"],
        result="INSUFFICIENT_DATA", rationale="пока мало фактов",
        confidence=0.3,
    )["id"]
    r = A.update_assessment(
        db, assessment_id=aid,
        result="COMPLIANT", rationale="новое подтверждение", confidence=0.9,
    )
    assert r["ok"] is True, r
    got = A.get_assessment(db, aid)
    assert got["result"] == "COMPLIANT"
    assert got["rationale"] == "новое подтверждение"
    assert abs(got["confidence"] - 0.9) < 1e-9
    # Pair scope unchanged
    assert got["requirement_id"] == seed["rid"]
    assert got["architecture_id"] == seed["arch"]


def test_update_replaces_evidence_atomically(db, seed) -> None:
    """ADR-001 §5/§9.5: providing `facts` replaces the whole evidence set
    atomically; a bad fact rolls back the entire update."""
    aid = A.create_assessment(
        db, requirement_id=seed["rid"], architecture_id=seed["arch"],
        result="PARTIAL", rationale="partly",
        facts=[
            {"fact_id": seed["f1"], "relation_type": "SUPPORTS"},
            {"fact_id": seed["f2"], "relation_type": "CONTEXT"},
        ],
    )["id"]
    assert len(A.assessment_evidence(db, aid)["items"]) == 2

    # Replace with a single CONTRADICTS fact.
    r = A.update_assessment(
        db, assessment_id=aid,
        result="INCOMPLIANT", rationale="теперь противоречие",
        facts=[{"fact_id": seed["f1"], "relation_type": "CONTRADICTS"}],
    )
    assert r["ok"] is True, r
    ev = A.assessment_evidence(db, aid)["items"]
    assert len(ev) == 1
    assert ev[0]["fact_id"] == seed["f1"]
    assert ev[0]["relation_type"] == "CONTRADICTS"
    got = A.get_assessment(db, aid)
    assert got["result"] == "INCOMPLIANT"

    # Empty list clears evidence.
    assert A.update_assessment(db, assessment_id=aid, facts=[])["ok"] is True
    assert A.assessment_evidence(db, aid)["items"] == []


def test_update_rejection_is_atomically_rolled_back(db, seed) -> None:
    """ADR-001 §5.4: a bad evidence fact must roll back the whole update,
    leaving prior result AND prior evidence untouched."""
    aid = A.create_assessment(
        db, requirement_id=seed["rid"], architecture_id=seed["arch"],
        result="COMPLIANT", rationale="ok",
        facts=[{"fact_id": seed["f1"], "relation_type": "SUPPORTS"}],
    )["id"]

    arch2 = S.create_architecture(db, name="Чужая арк 3", version="1.0")["id"]
    foreign = F.create_fact(
        db, architecture_id=arch2, source_id=seed["src"], fact_text="чужой"
    )["id"]
    # Mixed payload: valid field change + a foreign fact -> must all roll back.
    r = A.update_assessment(
        db, assessment_id=aid,
        result="INCOMPLIANT", rationale="must not land",
        facts=[{"fact_id": foreign, "relation_type": "SUPPORTS"}],
    )
    assert r["ok"] is False and r["code"] == "BAD_REQUEST", r
    # Prior state fully preserved (both verdict and evidence).
    got = A.get_assessment(db, aid)
    assert got["result"] == "COMPLIANT"
    assert got["rationale"] == "ok"
    ev = A.assessment_evidence(db, aid)["items"]
    assert len(ev) == 1 and ev[0]["fact_id"] == seed["f1"]

    # Bad relation_type / no fields also rejected cleanly.
    assert A.update_assessment(
        db, assessment_id=aid,
        facts=[{"fact_id": seed["f1"], "relation_type": "NOPE"}],
    )["code"] == "BAD_REQUEST"
    assert A.update_assessment(db, assessment_id=aid)["code"] == "BAD_REQUEST"
    assert A.update_assessment(db, assessment_id=999, result="COMPLIANT")[
        "code"] == "NOT_FOUND"


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
