# PostgreSQL Database Synchronizer

Инструмент для безопасной синхронизации схемы production PostgreSQL базы данных по образцу development базы данных.

## Что делает проект

Проект сравнивает две PostgreSQL базы данных:

- `dev` — эталонная база данных, в которой уже есть актуальные изменения схемы.
- `prod` — боевая база данных, которую нужно привести к структуре `dev` без повреждения существующих данных.

Инструмент умеет:

- анализировать различия между схемами двух БД;
- находить отсутствующие в production таблицы;
- находить отсутствующие в production колонки;
- генерировать безопасный план изменений;
- применять только безопасные изменения к production базе данных.

## Что считается безопасным изменением

В текущей реализации автоматически применяются только:

- создание отсутствующих таблиц;
- добавление отсутствующих колонок.

## Что НЕ применяется автоматически

Следующие изменения только фиксируются в отчёте, но не выполняются автоматически:

- удаление таблиц;
- удаление колонок;
- изменение типа существующей колонки;
- изменение `NULL / NOT NULL`;
- изменение `DEFAULT`;
- добавление потенциально опасных изменений, которые могут повредить production данные.

Такое поведение выбрано специально, чтобы не повредить боевую базу данных.

## Структура проекта

```text
app/
├── main.py
├── services/
│   └── db_synchronizer.py
└── utils/
    └── schema_tools.py
```

## Требования

- Python 3.11+
- PostgreSQL
- доступ к двум базам данных:
  - development database
  - production database

## Установка

### 1. Клонировать репозиторий

```bash
git clone https://github.com/kirill9114z/Test_DB_Synchronizer.git
cd Test_DB_Synchronizer
```

### 2. Создать виртуальное окружение

#### Linux / macOS
```bash
python -m venv .venv
source .venv/bin/activate
```

#### Windows
```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Установить зависимости

```bash
pip install -r requirements.txt
```

## Настройка переменных окружения

### 1. Создать `.env`

#### Linux / macOS
```bash
cp .env.example .env
```

#### Windows
Создайте файл `.env` вручную и скопируйте в него содержимое `.env.example`.

### 2. Заполнить `.env`

Пример содержимого:

```env
DEV_DB_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/dev_db
PROD_DB_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/prod_db
DB_SCHEMA=public
```

## Переменные окружения

| Переменная | Описание | Обязательна |
|-----------|----------|-------------|
| `DEV_DB_URL` | URL development базы данных | Да |
| `PROD_DB_URL` | URL production базы данных | Да |
| `DB_SCHEMA` | Имя схемы PostgreSQL | Нет |

Если `DB_SCHEMA` не задана, будет использоваться `public`.

## Как запускать

### Анализ различий

```bash
python app/main.py analyze
```

### Анализ конкретной схемы

```bash
python app/main.py analyze --schema public
```

### Анализ и применение безопасных изменений

```bash
python app/main.py execute
```

### Анализ и применение для конкретной схемы

```bash
python app/main.py execute --schema public
```

## Как работает analyze

Команда `analyze`:

1. подключается к `dev` и `prod` базам данных;
2. считывает информацию о таблицах и колонках;
3. определяет:
   - каких таблиц не хватает в production;
   - каких колонок не хватает в production;
   - какие колонки отличаются по типу, nullable или default;
   - какие таблицы существуют только в production;
4. формирует план изменений;
5. возвращает JSON-ответ со сводкой, SQL-командами и предупреждениями.

## Как работает execute

Команда `execute`:

1. вызывает `analyze`;
2. получает план безопасных изменений;
3. повторно проверяет актуальное состояние production базы;
4. выполняет только безопасные изменения в транзакции;
5. возвращает JSON-ответ с результатом выполнения.

Если во время выполнения происходит ошибка, транзакция откатывается.

## Пример результата analyze

```json
{
    "schema": "public",
    "missing_tables": ["orders"],
    "extra_tables": ["old_logs"],
    "missing_columns": [
        {
            "table": "users",
            "column": "phone",
            "type": "VARCHAR",
            "nullable": true,
            "default": null
        }
    ],
    "changed_columns": [
        {
            "table": "users",
            "column": "email",
            "dev": {
                "type": "VARCHAR(255)",
                "nullable": false,
                "default": null
            },
            "prod": {
                "type": "TEXT",
                "nullable": true,
                "default": null
            }
        }
    ],
    "skipped_changes": [],
    "warnings": [
        "Table exists only in production and will not be removed automatically: old_logs",
        "Column differs but will not be changed automatically: users.email"
    ],
    "sql_statements": [
        "CREATE TABLE public.orders (...);",
        "ALTER TABLE public.users ADD COLUMN phone VARCHAR;"
    ],
    "summary": {
        "missing_tables_count": 1,
        "extra_tables_count": 1,
        "missing_columns_count": 1,
        "changed_columns_count": 1,
        "skipped_changes_count": 0,
        "sql_statements_count": 2,
        "warnings_count": 2
    }
}
```

## Основные принципы безопасности

- Инструмент не удаляет таблицы автоматически.
- Инструмент не удаляет колонки автоматически.
- Инструмент не изменяет существующие данные в production.
- Инструмент не меняет автоматически несовпадающие типы колонок.
- Потенциально опасные изменения не выполняются, а только попадают в предупреждения.
- Все выполняемые изменения запускаются в транзакции.

## Ограничения текущей версии

Текущая версия ориентирована на безопасный MVP и не покрывает все возможные случаи схемных изменений.

Например, сейчас не реализованы:

- полная синхронизация индексов;
- полная синхронизация foreign keys;
- синхронизация constraints;
- обработка сложных зависимостей между объектами схемы;
- удаление лишних объектов из production;
- миграция данных между таблицами.
