# Business Policy Layer v1 Design

Дата: 2026-06-29  
Статус: архитектурный проект  
Проект: OSBB  
Путь в проекте: `Docs\Architecture\Services\business_policy_layer_v1_design.md`

---

## 1. Назначение

`Business Policy Layer` — это слой бизнес-правил, который принимает решение:

```text
можно ли выполнить действие по услуге
```

Он не заменяет:

- каталог услуг;
- кассу;
- заказы;
- склад;
- Telegram-интерфейс;
- будущий web-интерфейс.

Он является единым местом, где система отвечает на вопрос:

```text
ALLOW / WARN / BLOCK
```

---

## 2. Главный принцип

### Намерение не блокируется

Житель имеет право выразить желание:

```text
хочу новый пульт
хочу перепрошить пульт
хочу телефонный доступ
хочу другую услугу
```

Такое желание фиксируется как `Interest`.

### Действие регулируется политикой

Политики применяются не к желанию, а к реальному действию:

```text
создать заказ
выдать пульт
активировать телефон
подключить доступ
завершить услугу
```

Иначе говоря:

```text
Запрещаем не желание, а выполнение действия.
```

---

## 3. Текущая цепочка

```text
Resident
  ↓
Interest
  ↓
Operator / System
  ↓
Order
  ↓
Policy Check
  ↓
ALLOW / WARN / BLOCK
  ↓
Execution
```

---

## 4. Уже реализовано в v1

### 4.1 service_catalog v2

В `service_catalog` добавлены поля политики:

```text
access_policy_enabled
access_policy_scope
access_policy_mode
access_policy_message
manual_review_required
policy_updated_at
policy_updated_by
```

Режимы:

```text
NONE
WARN
BLOCK
```

---

### 4.2 service_access_policy.py

Создан рабочий модуль:

```text
service_access_policy.py
```

Основные функции:

```python
check_service_allowed(conn, apartment_number, service_code)

resolve_service_code(conn, service_item_code)

check_service_item_allowed(conn, apartment_number, service_item_code)

ensure_service_order_allowed(conn, apartment_number, service_item_code)
```

Основное исключение:

```python
ServiceAccessDenied
```

---

### 4.3 service_orders_core.create_service_order()

`create_service_order()` теперь вызывает:

```python
ensure_service_order_allowed(...)
```

до записи заказа в БД.

Принцип защиты:

```text
BLOCK / ERROR → заказ не создаётся
WARN → заказ создаётся, но политика возвращает предупреждение
ALLOW → заказ создаётся штатно
```

---

### 4.4 service_orders_workspace.py

Telegram workspace теперь умеет красиво показывать `ServiceAccessDenied` через:

```python
result_to_short_text(...)
```

---

## 5. Первая реализованная политика: DebtPolicy

Текущая политика проверяет задолженность.

Источник данных:

```text
charges
payment_allocations
service_catalog.access_policy_*
```

В версии v1 блокирующие источники задолженности:

```text
PARKING_DAY
PARKING_NIGHT
```

Не блокируют в v1:

```text
BARRIER_REPAIR
IMPROVEMENT
PARKING_EQUIPMENT
HISTORICAL_UNCLASSIFIED
```

---

## 6. Решения v1

### 6.1 Новый пульт

```text
TEST_REMOTE_NEW
TEST_REMOTE_REFURBISHED
```

Режим:

```text
BLOCK при задолженности PARKING
```

---

### 6.2 Перепрошивка собственного пульта

```text
TEST_REMOTE_REPROGRAM_OWN
```

Режим:

```text
WARN при задолженности PARKING
```

Оператор может принять ручное решение.

---

### 6.3 Телефонный доступ

```text
TEST_PHONE_ACCESS_CONNECT
BARRIER_PHONE_CONNECT
BARRIER_PHONE
```

Режим:

```text
BLOCK при задолженности PARKING
```

---

## 7. Будущие политики

`DebtPolicy` — первая политика, но не последняя.

Будущий `Business Policy Layer` должен поддерживать цепочку независимых проверок:

```text
DebtPolicy
InventoryPolicy
BoardDecisionPolicy
ResidentStatusPolicy
TechnicalPolicy
SeasonPolicy
TimePolicy
GlobalAccessPolicy
```

---

### 7.1 InventoryPolicy

Проверяет наличие ресурса:

```text
есть ли пульты на складе
есть ли свободные метки
есть ли техническая возможность подключения
```

---

### 7.2 BoardDecisionPolicy

Проверяет необходимость решения правления:

```text
требуется согласование
есть индивидуальный запрет
есть индивидуальное разрешение
```

---

### 7.3 ResidentStatusPolicy

Проверяет статус квартиры / жильца:

```text
квартира подтверждена
житель связан с квартирой
аккаунт активен
нет спорного статуса
```

---

### 7.4 TechnicalPolicy

Проверяет техническое состояние услуги:

```text
сервис временно отключён
нет связи с внешней системой
шлагбаум / GEOS / доступ на обслуживании
```

---

### 7.5 GlobalAccessPolicy

Будущий глобальный режим:

```text
OFF     — полная амнистия
WARN    — только предупреждения
ENFORCE — применение политик
```

Предполагаемое хранение:

```text
system_settings.access_policy_global_mode
```

---

## 8. Целевая архитектура

В будущем `ensure_service_order_allowed()` должен стать не одной проверкой, а конвейером политик.

```python
def ensure_service_order_allowed(...):
    context = build_policy_context(...)

    for policy in enabled_policies:
        result = policy.check(context)

        if result.decision == "BLOCK":
            raise ServiceAccessDenied(result)

        if result.decision == "WARN":
            context.add_warning(result)

    return context.final_result()
```

---

## 9. Единая точка входа

Для ядра заказов, Telegram и будущего web-интерфейса должна оставаться одна точка входа:

```python
ensure_service_order_allowed(...)
```

Внешние модули не должны знать:

- как считается задолженность;
- где лежит политика;
- какие таблицы используются;
- какие правила включены;
- какие политики появятся позже.

---

## 10. Принцип fail-fast

Если обязательный модуль политики недоступен, создание заказа должно быть остановлено.

Нельзя молча создавать заказы без политики.

В `service_orders_core.py` это уже закреплено:

```python
if ensure_service_order_allowed is None:
    raise RuntimeError(...)
```

---

## 11. UX-принципы

Каждая блокировка должна иметь понятное сообщение.

Плохо:

```text
PermissionError
```

Хорошо:

```text
По квартире есть задолженность за парковку.
Заказ нового пульта временно недоступен до оплаты или сверки.
```

---

## 12. Аналитика

Сохранение `Interest` даже при невозможности исполнения позволяет видеть реальный спрос:

```text
сколько жителей хотели услугу
сколько было заблокировано долгом
сколько ждёт решения правления
сколько невозможно из-за склада
сколько выполнено
```

Это важно для управления ОСББ.

---

## 13. Что не делаем в v1

В v1 не создаём полноценный движок правил.

Не вводим:

- универсальный DSL правил;
- сложные many-to-many policy tables;
- web-интерфейс настройки;
- глобальную системную политику;
- цепочку классов политик.

v1 остаётся простой:

```text
service_catalog policy fields
+
service_access_policy.py
+
ensure_service_order_allowed()
```

---

## 14. Следующие практические шаги

### Шаг 1

Завершить UX-интеграцию в `service_orders_workspace.py`.

### Шаг 2

Подключить понятную обработку `ServiceAccessDenied` к выдаче / активации.

### Шаг 3

Сделать тесты:

```text
BLOCK → заказ не создан
WARN → заказ создан
ALLOW → заказ создан
```

### Шаг 4

Подготовить `GlobalAccessPolicy` как v3-roadmap, но не внедрять сразу.

### Шаг 5

После стабилизации вернуться к рабочему месту кассира.

---

## 15. Архитектурный принцип

```text
Пользователь выражает намерение.
Система фиксирует намерение.
Политики регулируют выполнение.
Каждое ограничение объясняется.
```

---

## 16. ADR-кандидат

```text
ADR-0002

Название:
Business Policy Layer

Решение:
Ввести единый слой бизнес-политик для проверки выполнения действий по услугам.

Причина:
Запреты и ограничения не должны быть разбросаны по Telegram-хендлерам,
кассе, складу и сервисным модулям.

Последствие:
service_orders_core вызывает ensure_service_order_allowed(),
а конкретные политики развиваются внутри service_access_policy.py
и будущих policy-модулей.
```
