"""Tests for architecture_mcp.server: composition root and full tool inventory."""
from __future__ import annotations

import pytest

from architecture_mcp import server as SRV
from architecture_mcp.db import ENV_DB_PATH, Database

ALL_EXPECTED_TOOLS = {
    # sources.py
    "source_create", "source_get", "source_list",
    "npa_attach", "npa_get",
    "architecture_create", "architecture_get", "architecture_list",
    # requirements.py
    "requirement_create", "requirement_get", "requirement_list",
    "requirement_search", "requirement_update", "requirement_categories",
    "requirement_correct_quote",
    # facts.py
    "fact_create", "fact_get", "fact_list",
    "fact_search", "fact_update", "fact_categories",
    "fact_correct_quote",
    # categories.py
    "category_create", "category_get", "category_list",
    "category_search", "category_assign", "category_unassign",
    # assessments.py
    "assessment_create", "assessment_get", "assessment_list",
    "assessment_update",
    "assessment_attach_fact", "assessment_detach_fact", "assessment_facts",
}


def test_resolve_db_path_env(monkeypatch, tmp_path) -> None:
    p = tmp_path / "x.sqlite3"
    monkeypatch.setenv(ENV_DB_PATH, str(p))
    assert SRV._resolve_db_path(None) == str(p)


def test_resolve_db_path_cli_wins_over_env(monkeypatch, tmp_path) -> None:
    p1, p2 = tmp_path / "a.sqlite3", tmp_path / "b.sqlite3"
    monkeypatch.setenv(ENV_DB_PATH, str(p1))
    assert SRV._resolve_db_path(str(p2)) == str(p2)


def test_resolve_db_path_missing_raises(monkeypatch) -> None:
    monkeypatch.delenv(ENV_DB_PATH, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        SRV._resolve_db_path(None)
    assert ENV_DB_PATH in str(excinfo.value)


@pytest.mark.asyncio
async def test_build_server_registers_full_inventory(db: Database) -> None:
    mcp = SRV._build_server(db)
    names = {t.name for t in await mcp.list_tools()}
    missing = ALL_EXPECTED_TOOLS - names
    assert missing == set(), f"missing tools: {sorted(missing)}"
    extra = names - ALL_EXPECTED_TOOLS
    assert extra == set(), f"unexpected tools: {sorted(extra)}"
    assert len(names) == len(ALL_EXPECTED_TOOLS)


def test_db_path_env_e2e(tmp_path, monkeypatch) -> None:
    """A fresh Database must create the file from the env var path."""
    target = tmp_path / "nested" / "e2e.sqlite3"
    monkeypatch.setenv(ENV_DB_PATH, str(target))
    d = Database(SRV._resolve_db_path(None))
    d.initialize_schema()
    assert target.exists()
    d.close()
