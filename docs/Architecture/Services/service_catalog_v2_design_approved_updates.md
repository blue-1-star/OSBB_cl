# service_catalog v2 design — approved decisions

Дата: 2026-06-28  
Статус: уточнение к `service_catalog_v2_design.md`

## Утверждённые ответы по открытым вопросам

### 1. Должна ли задолженность по `BARRIER_REPAIR` блокировать доступ к пультам?

**Решение:** нет.

`BARRIER_REPAIR` не является блокирующим источником задолженности для пультов и телефонного доступа в версии v1.

### 2. Должна ли задолженность по `IMPROVEMENT` блокировать доступ к сервисам?

**Решение:** пока нет.

В версии v1 `IMPROVEMENT` не блокирует доступ к сервисам.  
В будущем возможны варианты, поэтому архитектура должна позволять включить такое правило позже без переделки кода.

### 3. Перепрошивка собственного пульта — мягкий или жёсткий запрет?

**Решение:** мягкий.

Для `TEST_REMOTE_REPROGRAM_OWN` используется режим:

```text
requires_no_debt = 1
debt_check_scope = PARKING
debt_block_mode = SOFT
manual_review_required = 1
```

Оператор видит предупреждение о задолженности, но может принять решение вручную.

### 4. Изменение телефонного номера — мягкий или жёсткий запрет?

**Решение:** мягкий.

Изменение телефонного номера не должно быть жёстко заблокировано в v1.  
Оператор должен видеть задолженность и принимать решение вручную.

### 5. Нужно ли правлению отдельное меню настройки этих полей через бота?

**Решение:** через бота — нет.

В будущем настройка политики задолженности нужна, но не через Telegram-бот.  
Целевой интерфейс — будущий веб-интерфейс администратора / правления.

## Итоговая политика v1

### Блокирующие источники задолженности v1

```text
PARKING_DAY
PARKING_NIGHT
```

### Не блокируют в v1

```text
BARRIER_REPAIR
IMPROVEMENT
PARKING_EQUIPMENT
HISTORICAL_UNCLASSIFIED
```

### Жёсткое ограничение

```text
TEST_REMOTE_NEW
TEST_REMOTE_REFURBISHED
TEST_PHONE_ACCESS_CONNECT
BARRIER_PHONE_CONNECT
BARRIER_PHONE
```

### Мягкое ограничение

```text
TEST_REMOTE_REPROGRAM_OWN
PHONE_ACCESS_CHANGE / изменение телефонного номера
```

## Обновление раздела 8 исходного design-документа

Рекомендуемая настройка v1:

| service_code | requires_no_debt | debt_check_scope | debt_block_mode | manual_review_required |
|---|---:|---|---|---:|
| `TEST_REMOTE_NEW` | 1 | `PARKING` | `HARD` | 0 |
| `TEST_REMOTE_REFURBISHED` | 1 | `PARKING` | `HARD` | 0 |
| `TEST_REMOTE_REPROGRAM_OWN` | 1 | `PARKING` | `SOFT` | 1 |
| `TEST_PHONE_ACCESS_CONNECT` | 1 | `PARKING` | `HARD` | 0 |
| `BARRIER_PHONE_CONNECT` | 1 | `PARKING` | `HARD` | 0 |
| `BARRIER_PHONE` | 1 | `PARKING` | `HARD` | 0 |
| `PARKING_DAY` | 0 | `NONE` | `NONE` | 0 |
| `PARKING_NIGHT` | 0 | `NONE` | `NONE` | 0 |
| `IMPROVEMENT` | 0 | `NONE` | `NONE` | 0 |
| `BARRIER_REPAIR` | 0 | `NONE` | `NONE` | 0 |
| `PARKING_EQUIPMENT` | 0 | `NONE` | `NONE` | 0 |
| `HISTORICAL_UNCLASSIFIED` | 0 | `NONE` | `NONE` | 0 |

## Следующее техническое действие

После внесения этих решений в основной документ можно готовить миграцию:

```text
MIGRATE_service_catalog_v2_policy_fields.py
```

Только для live-services sandbox DB. По умолчанию dry-run, запись только с `--apply`.
