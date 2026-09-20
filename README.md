# architecture-mcp

Локальный MCP-сервер для **верификации архитектуры ПО по требованиям**
(НПА, ТЗ, архитектурная документация, ADR).

- Хранилище — один локальный **SQLite**-файл (стандартный `sqlite3`, WAL).
- AI-агент работает **только через предметные MCP tools**; прямого доступа к
  SQL у агента нет, generic-инструмента `execute_sql` нет.
- Архитектура: **Modular Monolith + Composition Root + Explicit Module
  Registration** (см. `AGENTS.md` и `src/architecture_mcp/server.py`).

```
agent (Goose) → MCP (stdio) → server.py (composition root)
                                        ↓ register(mcp, db)
              sources / requirements / facts / categories / assessments
                                        ↓   (параметризованный SQL)
                                   db.py  →  SQLite
```

## Установка и запуск

```bash
uv sync
# Путь к БД задаётся переменной окружения (каталог создаётся автоматически):
export ARCH_MCP_DB="$PWD/data/arch.sqlite3"
uv run architecture-mcp          # транспорт stdio
```

Альтернативно: `uv run architecture-mcp --db-path /путь/arch.sqlite3`.

Проверка: `uv run pytest -q && uv run ruff check .`

Подключение из Goose: локальный stdio-сервер —
`command: uv, args: [run, architecture-mcp]`, `env: {ARCH_MCP_DB: ...}`.

## Модель данных

| Таблица                | Назначение                                      |
|-------------------------|-------------------------------------------------|
| `sources`               | Документ: НПА / ТЗ / архитектура / стандарт     |
| `npa`                   | Ревизиты НПА (1:1 к `sources`, тип `NPA`)       |
| `architectures`         | Анализированная архитектура                     |
| `requirements`          | Атомарное требование + происхождение            |
| `facts`                 | Атомарный факт архитектуры (привязан к arch)    |
| `categories`            | Единый справочник (иерархия, scope)             |
| `requirement_categories`| M:N требование ↔ категория (+ confidence)       |
| `fact_categories`       | M:N факт ↔ категория (+ confidence)             |
| `assessments`           | Вердикт по паре (requirement, architecture)     |
| `assessment_facts`      | М:N оценочные факты + отношение                 |

Отношение факта в оценке: `SUPPORTS` / `CONTRADICTS` / `CONTEXT`.
Результат оценки: `COMPLIANT` / `INCOMPLIANT` / `PARTIAL` / `NOT_APPLICABLE` /
`INSUFFICIENT_DATA`.

## Инструменты (32 tools)

### sources.py
| Tool | Назначение |
|---|---|
| `source_create` | Создать документ-источник (тип: NPA/TD/ARCH/STANDARD/INTERNAL) |
| `source_get` | Получить источник по id |
| `source_list` | Список источников (limit/offset) |
| `npa_attach` | Привязать/заменить ревизиты НПА к источнику |
| `npa_get` | Получить ревизиты НПА |
| `architecture_create` | Зарегистрировать архитектуру |
| `architecture_get` | Получить архитектуру |
| `architecture_list` | Список архитектур |

### requirements.py
| Tool | Назначение |
|---|---|
| `requirement_create` | Создать атомарное требование (из источника) |
| `requirement_get` | Получить требование |
| `requirement_list` | Список (фильтр по источнику, limit/offset) |
| `requirement_search` | Подстроковый поиск (регистронезависимый, включая кириллицу) |
| `requirement_update` | Частичное обновление |
| `requirement_categories` | Категории требования (читает M:N) |

### facts.py
| Tool | Назначение |
|---|---|
| `fact_create` | Создать атомарный факт (архитектура + источник) |
| `fact_get` | Получить факт |
| `fact_list` | Список (фильтр по арх/источнику) |
| `fact_search` | Поиск по тексту факта |
| `fact_update` | Частичное обновление |
| `fact_categories` | Категории факта (читает M:N) |

### categories.py
| Tool | Назначение |
|---|---|
| `category_create` | Создать категорию (scope: REQUIREMENT/FACT/BOTH/ASSESSMENT, parent) |
| `category_get` | Получить категорию (с названием родителя) |
| `category_list` | Справочник (фильтры scope/parent) |
| `category_search` | **Вызвать до создания новой категории** — поиск по имени/описанию |
| `category_assign` | Назначить категорию REQUIREMENT или FACT (upsert, confidence) |
| `category_unassign` | Снять категорию |

### assessments.py
| Tool | Назначение |
|---|---|
| `assessment_create` | Вердикт по (requirement, architecture) + опциональные факты одним транзактом |
| `assessment_get` | Получить оценку |
| `assessment_list` | Список (фильтры requirement/architecture/result) |
| `assessment_attach_fact` | Привязать факт: SUPPORTS/CONTRADICTS/CONTEXT (upsert) |
| `assessment_detach_fact` | Отвязать факт |
| `assessment_facts` | Доказательная база оценки |

## Типовой рабочий цикл агента

1. `source_create` (+ `npa_attach` для НПА) — зафиксировать документ.
2. `requirement_create` — атомарные требования из документа
   (`source_locator` + `source_quote` сохраняют происхождение).
3. `architecture_create` — архитектура, под проверку.
4. `fact_create` — атомарные факты архитектуры с источником.
5. `category_search` → при отсутствии `category_create` →
   `category_assign` для требований и фактов.
6. `assessment_create` — вердикт по паре
   (при нехватке данных — `INSUFFICIENT_DATA`), затем
   `assessment_attach_fact` с отношением SUPPORTS/CONTRADICTS/CONTEXT.

## Типы ответов

Все tools возвращают единый envelope:
- успех: `{"ok": true, ...payload}`
- ошибка: `{"ok": false, "error": "...", "code": "BAD_REQUEST|NOT_FOUND|CONFLICT|ERROR"}`.

## Ограничения (v1, см. AGENTS.md §9)

Нет FTS5/векторов, auth, audit-log, REST, фоновых задач.
Текстовый поиск — `LIKE` (регистронезависимый через UDF `ilower`).
Списковые операции — всегда `limit` (до 500) + `offset`.

## Структура

```
src/architecture_mcp/
├── server.py         # composition root (единственное место регистрации)
├── db.py             # SQLite: connection, PRAGMAs, схема, транзакции, ilower
├── models.py         # общие enum / лимиты / envelope ответов
├── sources.py        # источники, НПА, архитектуры
├── requirements.py   # требования
├── facts.py          # факты архитектуры
├── categories.py     # справочник категорий + назначение
└── assessments.py    # оценки соответствия + доказательная база
tests/                # pytest (доменные операции + MCP-регистрация)
```
