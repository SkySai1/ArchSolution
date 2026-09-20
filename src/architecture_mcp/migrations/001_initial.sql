-- Migration 001: initial schema (sources, npa, architectures, requirements,
-- facts, categories, M:N category joins, assessments, assessment_facts).
-- Applied once; tracked in schema_migrations. Idempotent DDL inside is a
-- safety net, not the migration mechanism itself.
CREATE TABLE IF NOT EXISTS sources (
                id          INTEGER PRIMARY KEY,
                source_type TEXT    NOT NULL,
                title       TEXT    NOT NULL,
                version     TEXT,
                file_path   TEXT,
                status      TEXT    NOT NULL DEFAULT 'ACTIVE',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS npa (
                source_id     INTEGER PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                document_type TEXT NOT NULL,
                number        TEXT,
                issuer        TEXT,
                status        TEXT NOT NULL DEFAULT 'ACTIVE'
            );
            CREATE INDEX IF NOT EXISTS idx_npa_status ON npa(status);

            CREATE TABLE IF NOT EXISTS architectures (
                id          INTEGER PRIMARY KEY,
                name        TEXT    NOT NULL,
                version     TEXT,
                description TEXT,
                status      TEXT    NOT NULL DEFAULT 'ACTIVE',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_architectures_name
                ON architectures(name, coalesce(version, ''));

            CREATE TABLE IF NOT EXISTS requirements (
                id              INTEGER PRIMARY KEY,
                source_id       INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                source_locator  TEXT,
                source_quote    TEXT,
                requirement_text TEXT   NOT NULL,
                normalized_text TEXT,
                status          TEXT    NOT NULL DEFAULT 'ACTIVE',
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_requirements_source ON requirements(source_id);
            CREATE INDEX IF NOT EXISTS idx_requirements_status ON requirements(status);

            CREATE TABLE IF NOT EXISTS facts (
                id              INTEGER PRIMARY KEY,
                architecture_id INTEGER NOT NULL REFERENCES architectures(id) ON DELETE CASCADE,
                source_id       INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                source_locator  TEXT,
                source_quote    TEXT,
                fact_text       TEXT    NOT NULL,
                normalized_text TEXT,
                status          TEXT    NOT NULL DEFAULT 'ACTIVE',
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_facts_arch ON facts(architecture_id);
            CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source_id);

            CREATE TABLE IF NOT EXISTS categories (
                id          INTEGER PRIMARY KEY,
                parent_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                name        TEXT    NOT NULL,
                description TEXT,
                scope       TEXT    NOT NULL DEFAULT 'BOTH',
                status      TEXT    NOT NULL DEFAULT 'ACTIVE',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_categories_name
                ON categories(name, coalesce(parent_id, 0));

            CREATE TABLE IF NOT EXISTS requirement_categories (
                requirement_id INTEGER NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
                category_id    INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                confidence     REAL    NOT NULL DEFAULT 1.0,
                PRIMARY KEY (requirement_id, category_id)
            );
            CREATE INDEX IF NOT EXISTS idx_rcat_category
                ON requirement_categories(category_id);

            CREATE TABLE IF NOT EXISTS fact_categories (
                fact_id    INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
                category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                confidence REAL    NOT NULL DEFAULT 1.0,
                PRIMARY KEY (fact_id, category_id)
            );
            CREATE INDEX IF NOT EXISTS idx_fcat_category
                ON fact_categories(category_id);

            CREATE TABLE IF NOT EXISTS assessments (
                id               INTEGER PRIMARY KEY,
                requirement_id   INTEGER NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
                architecture_id  INTEGER NOT NULL REFERENCES architectures(id) ON DELETE CASCADE,
                result           TEXT    NOT NULL,
                rationale        TEXT    NOT NULL,
                confidence       REAL    NOT NULL DEFAULT 1.0,
                created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_assessments_pair
                ON assessments(requirement_id, architecture_id);
            CREATE INDEX IF NOT EXISTS idx_assessments_arch
                ON assessments(architecture_id);

            CREATE TABLE IF NOT EXISTS assessment_facts (
                assessment_id INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
                fact_id       INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
                relation_type TEXT    NOT NULL CHECK (relation_type
                    IN ('SUPPORTS','CONTRADICTS','CONTEXT')),
                PRIMARY KEY (assessment_id, fact_id)
            );
            CREATE INDEX IF NOT EXISTS idx_afact_fact
                ON assessment_facts(fact_id);
