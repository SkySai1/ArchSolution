"""Composition root for the architecture-mcp local MCP server.

This file is the ONE place where the application is assembled:
- resolves the SQLite path (env var ``ARCH_MCP_DB`` or CLI argument),
- opens the database and applies the schema,
- registers every domain module's MCP tools,
- runs the MCP server over stdio (the default Goose transport).

No SQL and no domain business logic live here; domain modules own their
tools behind their ``register(mcp, db)`` entry point.

Usage (from ``pyproject.toml`` entry point ``architecture-mcp``):
    ARCH_MCP_DB=/var/path/arch.sqlite3 architecture-mcp
    architecture-mcp --db /var/path/arch.sqlite3
"""
from __future__ import annotations

import argparse
import os

from mcp.server.mcpserver import MCPServer

from . import (
    __version__,
    assessments,
    categories,
    facts,
    requirements,
    sources,
)
from .db import ENV_DB_PATH, Database


def _resolve_db_path(cli_value: str | None) -> str:
    """The CLI flag wins over the environment variable (which is required)."""
    if cli_value:
        return cli_value
    env_value = os.environ.get(ENV_DB_PATH)
    if not env_value:
        raise SystemExit(
            f"Database path not set: pass --db PATH or set {ENV_DB_PATH}=PATH"
        )
    return env_value


def _build_server(db: Database) -> MCPServer:
    """Create the MCP server and register every domain module's tools."""
    mcp = MCPServer("architecture-mcp", title="Architecture MCP",
                    version=__version__)
    sources.register(mcp, db)            # source_*, npa_*, architecture_*
    requirements.register(mcp, db)       # requirement_*
    facts.register(mcp, db)              # fact_*
    categories.register(mcp, db)         # category_*
    assessments.register(mcp, db)        # assessment_*
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="architecture-mcp",
        description="Local MCP server for architecture compliance knowledge (SQLite).",
    )
    parser.add_argument(
        "--db-path", "--db",
        dest="db_path",
        default=None,
        help=f"Path to the SQLite file. Default: env var {ENV_DB_PATH}. "
             "Directory is created on demand.",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="MCP transport (default: stdio, matching the Goose local server).",
    )

    args = parser.parse_args()
    db = Database(_resolve_db_path(args.db_path))
    db.initialize_schema()
    mcp = _build_server(db)
    try:
        mcp.run(transport=args.transport)
    finally:
        db.close()


if __name__ == "__main__":
    main()
