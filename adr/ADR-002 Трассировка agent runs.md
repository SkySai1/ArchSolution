# ADR-002. Трассировка AI-операций через agent_runs

**Статус:** Proposed
**Проект:** Architecture MCP
**Область:** AI provenance / traceability / SQLite
**Приоритет:** Обязательный для трассируемой эксплуатации

## 1. Контекст

Architecture MCP предназначен для работы через AI-агента.

AI может:

* извлекать требования;
* формировать нормализованные формулировки;
* извлекать архитектурные факты;
* классифицировать требования;
* классифицировать факты;
* создавать категории;
* выполнять assessment;
* повторно выполнять assessment после изменения данных.

При хранении только конечной записи невозможно определить:

```text
какой агент
какой моделью
в рамках какой операции
и когда

создал конкретный AI-generated результат
```

Для системы, используемой при анализе требований НПА и архитектуры, такая связь является существенной частью трассируемости.

---

## 2. Решение

В SQLite должна быть добавлена таблица:

```text
agent_runs
```

Одна запись соответствует одному логическому запуску или операции AI-агента.

---

## 3. Структура таблицы

Предлагаемая минимальная модель:

```sql
CREATE TABLE agent_runs (
    id               INTEGER PRIMARY KEY,

    agent            TEXT NOT NULL,
    model            TEXT,

    task_type        TEXT NOT NULL,

    parameters_json  TEXT,

    started_at       TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at      TEXT,

    status           TEXT NOT NULL DEFAULT 'RUNNING',

    error_message    TEXT
);
```

### Назначение полей

`id`

Уникальный идентификатор запуска.

---

`agent`

Имя или тип вызывающего агента.

Примеры:

```text
goose
architecture-analyzer
requirements-extractor
```

---

`model`

Модель, которая выполняла операцию.

Примеры:

```text
qwen3.8:27b
qwen3.6:35b-a3b-coding
devstral-small-2
```

Поле может быть `NULL`, если вызывающая сторона не передала сведения о модели.

---

`task_type`

Тип выполняемой операции.

Рекомендуемые значения:

```text
REQUIREMENT_EXTRACTION
FACT_EXTRACTION
CATEGORY_CLASSIFICATION
CATEGORY_CREATION
ASSESSMENT
REASSESSMENT
OTHER
```

На уровне первой версии допустимо хранить тип как `TEXT` с validation в MCP, не создавая отдельную справочную таблицу.

---

`parameters_json`

JSON с существенными параметрами запуска.

Например:

```json
{
  "source_id": 17,
  "architecture_id": 4
}
```

Поле не должно использоваться как основное структурированное хранилище бизнес-данных.

Его назначение — диагностический контекст запуска.

---

`started_at`

Время начала операции.

---

`finished_at`

Время завершения операции.

Для незавершённой операции допускается `NULL`.

---

`status`

Минимальные состояния:

```text
RUNNING
SUCCESS
FAILED
```

---

`error_message`

Текст причины ошибки при:

```text
status = FAILED
```

Не предназначен для хранения полного traceback.

---

## 4. MCP tools

Должны быть реализованы как минимум:

```text
agent_run_create
agent_run_finish
agent_run_get
```

### agent_run_create

Создаёт новый запуск.

Пример логики:

```text
Goose начинает извлечение требований
        ↓
agent_run_create
        ↓
id = 123
```

Возвращаемый `id` далее используется при создании AI-generated объектов.

---

### agent_run_finish

Завершает запуск.

Принимает как минимум:

```text
agent_run_id
status
error_message?
```

И устанавливает:

```text
finished_at
```

---

### agent_run_get

Возвращает сведения о конкретном запуске.

---

## 5. Связь agent_runs с бизнес-объектами

Следующие сущности должны иметь nullable FK:

```text
requirements.agent_run_id
facts.agent_run_id
assessments.agent_run_id
```

Если категории создаются AI-агентом, рекомендуется также:

```text
categories.agent_run_id
```

Для category assignments допускается добавить `agent_run_id` позднее вместе с полноценным аудитом классификации.

---

## 6. Правила использования

### 6.1. Данные из документа

Если AI извлекает requirement из документа:

```text
source
   ↓
requirement
   ├── source_quote
   ├── source_locator
   └── agent_run_id
```

В результате сохраняются две независимые линии происхождения:

```text
Что является доказательством?
→ source / quote / locator

Кто сделал интерпретацию?
→ agent_run
```

---

### 6.2. Архитектурный факт

```text
fact
├── architecture
├── source
└── agent_run
```

`source` отвечает за доказательную основу факта.

`agent_run` — за происхождение AI-интерпретации.

---

### 6.3. Assessment

```text
assessment
├── requirement
├── architecture
├── evidence facts
├── rationale
└── agent_run
```

При reassessment должен записываться `agent_run_id` последней операции оценки.

Полная история всех предыдущих версий assessment этим ADR не вводится.

Если такая история потребуется, она должна быть оформлена отдельным ADR.

---

## 7. Agent run не заменяет источник

Наличие:

```text
agent_run_id
```

не должно считаться доказательством факта или требования.

Для requirement доказательством остаются:

```text
source
source_locator
source_quote
```

Для архитектурного факта — соответствующий source и его положение в документе.

`agent_runs` отвечает только на вопрос:

> «Какая AI-операция породила или изменила эту интерпретацию?»

---

## 8. Транзакционная граница

Не требуется держать один SQLite transaction открытым в течение всей работы LLM.

Последовательность может быть:

```text
agent_run_create
      ↓
AI выполняет анализ
      ↓
несколько MCP write operations
      ↓
agent_run_finish
```

Каждая бизнес-операция продолжает использовать собственную короткую SQLite-транзакцию.

---

## 9. Поведение при аварийном завершении агента

Допускается сохранение:

```text
status = RUNNING
finished_at = NULL
```

если агент или Goose аварийно завершили работу до вызова `agent_run_finish`.

Такой запуск должен рассматриваться как незавершённый.

Автоматическая очистка зависших запусков в рамках данного ADR не требуется.

---

## 10. Индексы

Необходимо добавить:

```sql
CREATE INDEX idx_agent_runs_status
    ON agent_runs(status);

CREATE INDEX idx_agent_runs_task_type
    ON agent_runs(task_type);
```

Для FK:

```text
requirements.agent_run_id
facts.agent_run_id
assessments.agent_run_id
```

также рекомендуется создать индексы.

---

## 11. Foreign key semantics

При удалении `agent_run` связанные бизнес-объекты не должны удаляться.

Предпочтительно:

```sql
agent_run_id INTEGER
    REFERENCES agent_runs(id)
    ON DELETE SET NULL
```

Однако физическое удаление `agent_runs` в штатном MCP API предоставлять не требуется.

---

## 12. Миграция

Таблица должна добавляться отдельной schema migration, например:

```text
003_agent_runs.sql
```

Та же миграция либо последующие миграции должны добавить:

```text
requirements.agent_run_id
facts.agent_run_id
assessments.agent_run_id
categories.agent_run_id
```

без потери существующих данных.

Для существующих записей:

```text
agent_run_id = NULL
```

является допустимым состоянием.

---

## 13. Тестирование

Необходимо проверить следующие сценарии:

```text
1. agent_run_create создаёт RUNNING run.

2. agent_run_finish SUCCESS устанавливает finished_at.

3. agent_run_finish FAILED сохраняет error_message.

4. requirement может ссылаться на agent_run.

5. fact может ссылаться на agent_run.

6. assessment может ссылаться на agent_run.

7. существующие записи без agent_run остаются валидными.

8. удаление agent_run не должно удалить requirement/fact/assessment.

9. после перезапуска MCP трассировка сохраняется.
```

---

## 14. Итоговая цепочка трассируемости

После реализации двух ADR система должна позволять восстановить:

```text
Requirement
    ↓
Source
    ↓
source_locator
    ↓
source_quote
    ↓
agent_run
    ↓
model / agent / task

             +

Architecture
    ↓
Facts
    ↓
Sources
    ↓
agent_runs

             ↓

Assessment
    ↓
SUPPORTS / CONTRADICTS / CONTEXT
    ↓
Facts
    ↓
Sources

             +

Assessment
    ↓
agent_run
    ↓
AI operation
```

Главный принцип:

```text
исходный документ
≠
AI-интерпретация
≠
результат оценки
```

Все три уровня должны оставаться раздельными и трассируемыми.
