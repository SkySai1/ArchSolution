# AGENTS

This file is the operating manual for any AI agent (Goose or otherwise) working
on the `architecture-mcp` codebase. Read it before writing code or editing files.

## 1. What this project is

`architecture-mcp` is a **local MCP server** that stores the knowledge needed
to verify a software architecture against requirements (НПА, ТЗ, ADRs).

It exposes domain-scoped MCP tools and stores everything in **SQLite**.
There is no UI, no network API, no ORM.

## 2. Hard rules

Do not deviate from these without an explicit, documented exception:

1. **AI never talks to SQLite directly.** Only domain modules execute SQL.
2. **No `execute_sql()` tool.** Every public tool is domain-scoped (create a
   requirement, get a fact, create an assessment, ...).
3. **Parameterized queries only.** No f-string SQL, no `.format`, no
   string-concatenated user data in SQL.
4. **Transactions for composite writes.** Any tool that touches more than one
   table must roll back atomically on failure.
5. **Limit every list endpoint.** `limit` (+ optional `offset`) is mandatory.
6. **Memory ceiling.** Steady-state RSS ≤ 128 MB. Above 256 MB without an
   explicitly large operation is a defect.
7. **No heavy dependencies.** No ORM, no vector DB, no Redis, no Kafka.
   Allowed runtime deps: `mcp` (and its transitive deps). Nothing else unless
   a functional requirement demands it.
8. **Small total footprint.** All source code + this doc should fit in ~48K
   tokens. Do not inflate files with boilerplate.
9. **One file = one responsibility.** Do not create `repository`, `manager`,
   `service`, `factory` layers that only forward calls.
10. **Atomic functions.** Every domain function does exactly one thing.
    `create_requirement`, `search_facts`, `assign_category` — not
    `process_requirement_workflow`.

## 3. Architecture style

**Modular Monolith + Composition Root + Explicit Module Registration.**

```
server.py           # composition root — the only place where modules are registered
 ├─ db.py           # InfrastructureLayer: connection, PRAGMAs, schema, tx
 ├─ models.py       # Shared enums / result types (dataclasses or plain strings)
 ├─ sources.py
 ├─ requirements.py
 ├─ facts.py
 ├─ categories.py
 └─ assessments.py
```

Dependency direction is strict and one-directional:

```
domain modules  →  db.py  →  sqlite3
     │
     └──  models.py (shared types only)
```

A domain module may import **another** domain module's public function only
when both operations belong to the same write path (e.g. `create_fact`
validating the architecture). Any other cross-import is a bug.

`server.py` must be openable and understood alone: it lists **every**
registered MCP tool of the app.

## 4. Module shape

Every domain module exposes a `register()` function:

```python
"""Domain module for requirements (create, search, categorize)."""

def register(mcp, db):
    def _create(source_id: int, requirement_text: str, source_locator: str | None = None,
                source_quote: str | None = None) -> dict:
        ...

    @mcp.tool()
    def requirement_create(source_id: int,
                           requirement_text: str,
                           source_locator: str | None = None,
                           source_quote: str | None = None) -> dict:
        """Create an atomic requirement from a source document."""
        return requirement_create_impl(db, source_id, requirement_text,
                                       source_locator, source_quote)
```

Rules:

- `register(mcp, db)` is the **only** public entry of a module.
- Each MCP tool wraps a small atomic `_impl` that takes `db` as its first
  argument. This keeps the SQL close to the semantics and keeps tools thin.
- Tools return **plain dicts** (MCP-friendly) — no dataclasses, no ORM models.
- Every file starts with a one-line module docstring.
- No `if __name__ == "__main__"` in domain modules.

## 5. Data model (v1)

Tables (all `INTEGER PRIMARY KEY` unless noted):

| Table                    | Purpose                                            |
|--------------------------|----------------------------------------------------|
| `sources`                | A document (НПА, ТЗ, architecture doc, ADR, ...)   |
| `npa`                    | 1:1 extension on `sources.source_type = 'NPA'`     |
| `architectures`          | The architecture under analysis                    |
| `requirements`           | Atomic requirement, `source_id` FK mandatory       |
| `facts`                  | Atomic architectural fact, `architecture_id` FK    |
| `categories`             | Unified category tree (`parent_id` self-FK)        |
| `requirement_categories` | M:N join, composite PK `(requirement_id, category_id)` |
| `fact_categories`        | M:N join, composite PK `(fact_id, category_id)`    |
| `assessments`            | One row = one (requirement, architecture) verdict  |
| `assessment_facts`       | M:N join, `relation_type IN ('SUPPORTS','CONTRADICTS','CONTEXT')` |

Invariants (enforced by schema + module checks):

- `assessment` always has **exactly one** `requirement_id` and **exactly one**
  `architecture_id`.
- `assessment.result` is one of
  `COMPLIANT | INCOMPLIANT | PARTIAL | UNKNOWN | INSUFFICIENT_DATA`.
- `facts.architecture_id` is mandatory — a fact without an architecture is a
  bug.
- Composite join tables use `UNIQUE` on the pair.
- `PRAGMA foreign_keys = ON` is applied by `db.py` on every connection.

## 6. Persistence details

- **Pickle-free, standard `sqlite3`** (stdlib).
- **PRAGMAs** (applied once per connection, in `db.py`):
  - `foreign_keys = ON`
  - `journal_mode = WAL`
  - `synchronous = NORMAL`
  - `busy_timeout = 5000`
- **Row factory**: `sqlite3.Row` (dict-friendly access).
- **Transactions**: `with db.transaction() as tx:` — context manager that
  commits on success, rolls back on exception.
- **List endpoints**: every one that returns a list takes `limit` (default
  50, max 500) and optional `offset`.
- **Text search**: v1 uses `LIKE` on the stored text; no FTS5 / vectors.
- **Path to DB**: env var `ARCH_MCP_DB` (absolute path to the SQLite file).
  Directory is created on demand.

## 7. Testing

- `pytest` + `pytest-asyncio`.
- Each domain module has a test that:
  1. creates a temp SQLite file,
  2. instantiates the module against a real `Database`,
  3. exercises **at least one write + one read** through plain Python calls
     (not via the MCP transport),
  4. asserts the DB state directly.
- Integration tests for `register()` exist so that every module can be called
  via `mcp.list_tools()` and the tool is registered with the expected name.

## 8. Git discipline

- **Small, atomic commits.** One logical change → one commit.
- Conventional-commit style:
  - `feat(sources): add source creation`
  - `feat(db): apply PRAGMAs and create schema`
  - `fix(requirements): roll back on FK violation`
  - `refactor(server): isolate registration into helpers`
  - `test(facts): cover fact creation with category`
  - `docs: document tool inventory in README`
- Before every commit:
  ```bash
  uv run ruff check .
  uv run pytest -q
  ```
- No force-push. No amend on shared branches.
- Working tree must be clean between commits.

## 9. Non-goals (explicit)

Things that must NOT be added for v1:

- [ ] `execute_sql()` or any generic query tool.
- [ ] Authentication / multi-tenant isolation.
- [ ] Full-text search (FTS5 / Trigram).
- [ ] Embeddings / vector search.
- [ ] Audit log table.
- [ ] Category synonyms / aliases.
- [ ] Cross-architecture fact comparisons (a fact belongs to ONE architecture).
- [ ] Background jobs, workers, message queues.
- [ ] REST / gRPC surface.

If a need for any of these appears, first write a short `adr/` note, then
revisit.

## 10. Definition of done for a tool

A tool is "done" when:

- [ ] It is registered from its domain module's `register()`.
- [ ] It has a one-sentence `docstring` describing what it does.
- [ ] Every list endpoint has `limit` (and `offset` where sensible).
- [ ] All writes run through `db.transaction()`.
- [ ] All reads use parameterized queries.
- [ ] Returns a plain `dict` with stable keys.
- [ ] At least one `pytest` case covers the happy path.
- [ ] `ruff check` passes on the module.
