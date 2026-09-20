"""Tests for architecture_mcp.categories (directory + assignment)."""
from __future__ import annotations

from architecture_mcp import categories as C
from architecture_mcp import facts as F
from architecture_mcp import requirements as R
from architecture_mcp import sources as S


def _seed(db) -> tuple[int, int, int, int]:
    """Create one source, architecture, requirement and fact; return ids."""
    src = S.create_source(db, source_type="TD", title="ТЗ")["id"]
    arch = S.create_architecture(db, name="Арх", version="1")["id"]
    rid = R.create_requirement(db, source_id=src, requirement_text="req text")["id"]
    fid = F.create_fact(db, architecture_id=arch, source_id=src, fact_text="fact text")["id"]
    return src, arch, rid, fid


def test_create_and_get_with_parent(db) -> None:
    top = C.create_category(db, name="Безопасность")
    assert top["ok"] is True
    sub = C.create_category(db, name="Аутентификация", parent_id=top["id"])
    assert sub["ok"] is True
    got = C.get_category(db, sub["id"])
    assert got["ok"] is True
    assert got["parent_name"] == "Безопасность"


def test_category_name_unique_per_parent(db) -> None:
    assert C.create_category(db, name="Данные")["ok"] is True
    r = C.create_category(db, name="Данные")
    assert r["ok"] is False
    assert r["code"] == "CONFLICT"
    # but a child with the same name under a parent is allowed
    parent = C.create_category(db, name="Группа")
    r2 = C.create_category(db, name="Данные", parent_id=parent["id"])
    assert r2["ok"] is True


def test_bad_scope_and_status(db) -> None:
    assert C.create_category(db, name="X", scope="WRONG")["code"] == "BAD_REQUEST"
    assert C.create_category(db, name="X", status="WRONG")["code"] == "BAD_REQUEST"
    assert C.create_category(db, name="  ")["code"] == "BAD_REQUEST"


def test_parent_must_exist(db) -> None:
    r = C.create_category(db, name="Child", parent_id=999)
    assert r["ok"] is False
    assert r["code"] == "NOT_FOUND"


def test_list_filters_by_scope(db) -> None:
    C.create_category(db, name="A", scope="REQUIREMENT")
    C.create_category(db, name="B", scope="FACT")
    C.create_category(db, name="C", scope="BOTH")
    req_only = C.list_categories(db, scope="REQUIREMENT")
    assert [i["name"] for i in req_only["items"]] == ["A"]
    assert C.list_categories(db, scope="NOPE")["code"] == "BAD_REQUEST"


def test_search_case_insensitive_and_escaping(db) -> None:
    C.create_category(db, name="ПДн", description="Персональные данные")
    C.create_category(db, name="100% Coverage")
    res = C.search_categories(db, query="пдн")
    assert len(res["items"]) == 1
    res2 = C.search_categories(db, query="100%")
    assert len(res2["items"]) == 1
    assert res2["items"][0]["name"] == "100% Coverage"


def test_assign_to_requirement_and_visible(db) -> None:
    src, arch, rid, _fid = _seed(db)
    cat = C.create_category(db, name="Хранение", scope="BOTH")
    a = C.assign_category(db, category_id=cat["id"], requirement_id=rid, confidence=0.9)
    assert a["ok"] is True
    assert a["target_id"] == rid
    got = R.list_requirement_categories(db, rid)
    assert len(got["items"]) == 1
    assert got["items"][0]["confidence"] == 0.9
    assert got["items"][0]["name"] == "Хранение"


def test_assign_to_fact_upsert_confidence(db) -> None:
    _src, _arch, _rid, fid = _seed(db)
    cat = C.create_category(db, name="Стек", scope="FACT")
    assert C.assign_category(db, category_id=cat["id"], fact_id=fid)["ok"] is True
    r = C.assign_category(db, category_id=cat["id"], fact_id=fid, confidence=0.5)
    assert r["ok"] is True and r["confidence"] == 0.5  # upsert, not duplicate
    rows = F.list_fact_categories(db, fid)["items"]
    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.5


def test_assign_scope_rules(db) -> None:
    _src, _arch, rid, fid = _seed(db)
    req_cat = C.create_category(db, name="OnlyReq", scope="REQUIREMENT")["id"]
    fact_cat = C.create_category(db, name="OnlyFact", scope="FACT")["id"]
    r1 = C.assign_category(db, category_id=req_cat, fact_id=fid)
    assert r1["ok"] is False and r1["code"] == "BAD_REQUEST"
    r2 = C.assign_category(db, category_id=fact_cat, requirement_id=rid)
    assert r2["ok"] is False and r2["code"] == "BAD_REQUEST"
    assert C.assign_category(db, category_id=fact_cat, fact_id=fid)["ok"] is True


def test_assign_bad_input(db) -> None:
    cat = C.create_category(db, name="OK")["id"]
    _src, _arch, rid, fid = _seed(db)
    assert C.assign_category(db, category_id=cat)["code"] == "BAD_REQUEST"
    both = C.assign_category(db, category_id=cat, requirement_id=rid, fact_id=fid)
    assert both["code"] == "BAD_REQUEST"
    assert C.assign_category(db, category_id=999, requirement_id=rid)["code"] == "NOT_FOUND"
    missing_target = C.assign_category(db, category_id=cat, requirement_id=999)
    assert missing_target["ok"] is False and missing_target["code"] == "NOT_FOUND"
    bad_conf = C.assign_category(db, category_id=cat, requirement_id=rid, confidence=1.5)
    assert bad_conf["code"] == "BAD_REQUEST"


def test_unassign(db) -> None:
    _src, _arch, rid, _fid = _seed(db)
    cat = C.create_category(db, name="X")
    assert C.assign_category(db, category_id=cat["id"], requirement_id=rid)["ok"] is True
    r = C.unassign_category(db, category_id=cat["id"], requirement_id=rid)
    assert r["ok"] is True and r["deleted"] is True
    assert R.list_requirement_categories(db, rid)["items"] == []
    again = C.unassign_category(db, category_id=cat["id"], requirement_id=rid)
    assert again["code"] == "NOT_FOUND"
    bad = C.unassign_category(db, category_id=cat["id"])
    assert bad["code"] == "BAD_REQUEST"
