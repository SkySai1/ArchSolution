"""Shared pytest fixtures for architecture_mcp tests."""
from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

from architecture_mcp.db import Database


@pytest.fixture
def db_path() -> pathlib.Path:
    """A unique temporary SQLite file path (inside pytest's tmp factory)."""
    fd, path = tempfile.mkstemp(suffix=".sqlite3", prefix="arch_mcp_")
    os.close(fd)
    os.unlink(path)  # Database will create the file itself
    return pathlib.Path(path)


@pytest.fixture
def db(db_path: pathlib.Path) -> Database:
    d = Database(db_path)
    d.initialize_schema()
    yield d
    d.close()
