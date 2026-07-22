<!-- BEGIN: DEVELOPMENT-DOCS-V1 -->
# Query Library

## Назначение

Query Library — библиотека именованных диагностических SQL-запросов для
просмотра сложных связей в нормализованной базе OSBB.

## Цели

- объединять данные из нескольких таблиц;
- показывать неочевидные связи;
- ускорять диагностику;
- предоставлять повторно используемые отчеты.

## Безопасность

Все запросы по умолчанию только для чтения. Базовый разрешенный класс:

```sql
SELECT ...
```

## Категории

- View;
- Control;
- Analytics;
- Finance;
- Audit.

## Примеры имен

```text
parking.multiple_cars
parking.unknown_vehicle_fragments
finance.debtors
finance.payments_by_period
audit.deleted_today
apartments.without_residents
```

## Будущий объект запроса

- уникальное имя;
- описание;
- категория;
- SQL;
- параметры;
- ожидаемые колонки;
- уровень доступа;
- `read_only`;
- примеры использования.

## Интеграция с Assistant

```text
assistant query list
assistant query show parking.multiple_cars
assistant query run parking.multiple_cars
assistant query run finance.debtors --param period=2026-07
```

## Эволюция

```text
Loose SQL files
      ↓
Query Library
      ↓
Named Query Objects
      ↓
Query Engine
      ↓
Assistant / UI / Reports
```
<!-- END: DEVELOPMENT-DOCS-V1 -->
