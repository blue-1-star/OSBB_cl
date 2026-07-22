# Словарь данных OSBB (Data Dictionary)

**Версия:** 1.0  
**Дата обновления:** 2026-07-14  
**Назначение:** Единый справочник по структуре базы данных проекта OSBB.

---

## 1. Таблица `apartments`

| Колонка | Тип | Обязательное | Описание |
|---------|-----|--------------|----------|
| `id` | INTEGER | ✅ | Уникальный идентификатор (PK) |
| `apartment_number` | TEXT | ✅ | Номер квартиры/помещения |
| `entrance` | TEXT | ❌ | Номер подъезда |
| `unit_type` | TEXT | ❌ | Тип: RESIDENTIAL, COMMERCIAL, PARKING |
| `record_status` | TEXT | ❌ | ACTIVE, ARCHIVED, MERGED |
| `display_name` | TEXT | ❌ | Отображаемое имя |
| `created_at` | TEXT | ❌ | Дата создания |

**Связи:** apartments.id -> resident_accounts.apartment_id, vehicles.apartment_id

---

## 2. Таблица `resident_accounts`

| Колонка | Тип | Обязательное | Описание |
|---------|-----|--------------|----------|
| `id` | INTEGER | ✅ | Уникальный идентификатор (PK) |
| `telegram_user_id` | INTEGER | ✅ | Telegram ID |
| `telegram_username` | TEXT | ❌ | Username |
| `telegram_first_name` | TEXT | ❌ | Имя |
| `telegram_last_name` | TEXT | ❌ | Фамилия |
| `language_code` | TEXT | ❌ | Язык: ru, uk, en |
| `apartment_id` | INTEGER | ❌ | Ссылка на квартиру |
| `role` | TEXT | ❌ | resident, admin, super_admin, guard, operator |
| `status` | TEXT | ❌ | new, apartment_confirmed, operator_verified, blocked |
| `verified_at` | TEXT | ❌ | Дата подтверждения |

**Связи:** resident_accounts.apartment_id -> apartments.id

---

## 3. Таблица `service_catalog`

| Колонка | Тип | Обязательное | Описание |
|---------|-----|--------------|----------|
| `id` | INTEGER | ✅ | Уникальный идентификатор (PK) |
| `service_code` | TEXT | ✅ | Код услуги (например, PARKING_DAY) |
| `service_name` | TEXT | ✅ | Название услуги |
| `service_group` | TEXT | ❌ | MONTHLY, FUNDRAISING, COMMERCIAL, ACCESS_CONTROL |
| `service_type` | TEXT | ❌ | MONTHLY, ONE_TIME, FUNDRAISING, COMMERCIAL |
| `category` | TEXT | ❌ | PARKING, ACCESS, IMPROVEMENT, EQUIPMENT, BARRIER |
| `is_active` | INTEGER | ❌ | Доступна (1 — да) |
| `access_policy_enabled` | INTEGER | ❌ | Включена политика доступа |
| `access_policy_mode` | TEXT | ❌ | BLOCK, ALLOW, WARN |
| `access_policy_scope` | TEXT | ❌ | На что влияет политика (`PARKING`) |
| `access_policy_message` | TEXT | ❌ | Сообщение при блокировке |
| `manual_review_required` | INTEGER | ❌ | Требуется ручная проверка |
| `policy_updated_at` | TEXT | ❌ | Дата обновления политики |
| `policy_updated_by` | TEXT | ❌ | Кто обновил |

**Связи:** service_catalog.service_code -> payments.base_service_code

---

## 4. Таблица `vehicles`

| Колонка | Тип | Обязательное | Описание |
|---------|-----|--------------|----------|
| `id` | INTEGER | ✅ | Уникальный идентификатор (PK) |
| `apartment_id` | INTEGER | ✅ | Ссылка на квартиру |
| `license_plate` | TEXT | ❌ | Номер автомобиля |
| `license_plate_normalized` | TEXT | ❌ | Нормализованный номер |
| `car_model` | TEXT | ❌ | Марка и модель |
| `car_model_normalized` | TEXT | ❌ | Нормализованная марка |
| `parking_time` | TEXT | ❌ | Day, Night, Inactive, NULL |

**Связи:** vehicles.apartment_id -> apartments.id, vehicles.id -> payments.vehicle_id

---

## 5. Таблица `payments`

| Колонка | Тип | Обязательное | Описание |
|---------|-----|--------------|----------|
| `id` | INTEGER | ✅ | Уникальный идентификатор (PK) |
| `payment_date` | TEXT | ❌ | Дата платежа |
| `apartment_number` | TEXT | ✅ | Номер квартиры |
| `vehicle_id` | INTEGER | ❌ | Ссылка на автомобиль |
| `amount` | REAL | ✅ | Сумма |
| `currency` | TEXT | ✅ | UAH |
| `payment_method` | TEXT | ❌ | cash, card, bank |
| `base_service_code` | TEXT | ❌ | Код услуги из каталога |
| `candidate_id` | INTEGER | ❌ | Ссылка на vehicle_candidates.id |

---

## 6. Таблица `vehicle_candidates`

**Назначение:** Хранит информацию об автомобилях, которые ещё не подтверждены.

| Колонка | Тип | Обязательное | Описание |
|---------|-----|--------------|----------|
| `id` | INTEGER | ✅ | Уникальный идентификатор (PK) |
| `license_plate` | TEXT | ✅ | Номер автомобиля |
| `license_plate_normalized` | TEXT | ✅ | Нормализованный номер |
| `car_model` | TEXT | ❌ | Марка и модель |
| `apartment_id` | INTEGER | ❌ | Ссылка на квартиру (может быть NULL) |
| `apartment_number` | TEXT | ❌ | Номер квартиры |
| `status` | TEXT | ✅ | PENDING, RESOLVED, REJECTED, MERGED |
| `resolved_vehicle_id` | INTEGER | ❌ | Ссылка на vehicles.id |
| `created_by` | INTEGER | ❌ | ID создателя |
| `created_at` | TEXT | ❌ | Дата создания |
| `comment` | TEXT | ❌ | Комментарий оператора |
| `source` | TEXT | ❌ | cashier, telegram, tbot |

**Жизненный цикл:** PENDING -> RESOLVED / REJECTED
## 7. Таблица `service_items` (Цены на услуги)

**Назначение:** Хранит динамические тарифы (цены) для услуг из `service_catalog`. Используется кассой для автоматической подстановки суммы.

| Колонка | Тип | Обязательное | Описание |
|---------|-----|--------------|----------|
| `id` | INTEGER | ✅ | Уникальный ID (PK) |
| `service_item_code` | TEXT | ✅ | Код тарифа (уникальный) |
| `service_code` | TEXT | ✅ | Ссылка на услугу из `service_catalog` |
| `service_item_name` | TEXT | ✅ | Название для отображения |
| `service_type` | TEXT | ✅ | Тип услуги |
| `period_code` | TEXT | ❌ | Период действия тарифа |
| `sequence_no` | INTEGER | ❌ | Порядковый номер |
| `amount_default` | REAL | ❌ | Цена по умолчанию (грн) |
| `currency` | TEXT | ❌ | Валюта (UAH) |
| `date_from` | TEXT | ❌ | Дата начала действия |
| `date_to` | TEXT | ❌ | Дата окончания действия |
| `status` | TEXT | ❌ | `active`, `inactive`, `archived` |
| `is_active` | INTEGER | ❌ | 1 — активен |
| `description` | TEXT | ❌ | Описание |
| `comment` | TEXT | ❌ | Комментарий |
| `created_at` | TEXT | ❌ | Дата создания |
| `updated_at` | TEXT | ❌ | Дата обновления |

**Связи:** `service_items.service_code` → `service_catalog.service_code`

---

## 8. Таблица `payment_notices` (Уведомления жителей)

**Назначение:** Хранит уведомления жителей о намерении оплатить (наличные или банк). Используется кассой v2.

| Колонка | Тип | Обязательное | Описание |
|---------|-----|--------------|----------|
| `id` | INTEGER | ✅ | Уникальный ID (PK) |
| `notice_number` | TEXT | ✅ | Номер уведомления |
| `notice_type` | TEXT | ✅ | `CASH_HANDOVER` или `BANK_TRANSFER` |
| `notice_status` | TEXT | ✅ | `NEW`, `CONFIRMED`, `REJECTED` |
| `resident_account_id` | INTEGER | ✅ | Ссылка на жителя |
| `telegram_user_id` | TEXT | ✅ | Telegram ID |
| `apartment_id` | INTEGER | ❌ | Ссылка на квартиру |
| `apartment_number` | TEXT | ❌ | Номер квартиры |
| `declared_cashbox_code` | TEXT | ❌ | Касса |
| `declared_period_code` | TEXT | ❌ | Период |
| `declared_service_code` | TEXT | ❌ | Код услуги |
| `declared_amount` | REAL | ❌ | Заявленная сумма |
| `resident_comment` | TEXT | ❌ | Комментарий жителя |
| `operator_id` | TEXT | ❌ | ID оператора |
| `operator_note` | TEXT | ❌ | Заметка оператора |
| `reviewed_at` | TEXT | ❌ | Дата проверки |
| `confirmed_at` | TEXT | ❌ | Дата подтверждения |
| `rejected_at` | TEXT | ❌ | Дата отклонения |
| `created_at` | TEXT | ❌ | Дата создания |
| `updated_at` | TEXT | ❌ | Дата обновления |

**Связи:** `payment_notices.resident_account_id` → `resident_accounts.id`

---

## 9. Таблица `cashbox_operations` (Кассовые операции)

**Назначение:** Хранит все движения денег по кассам (приход, расход, переводы).

| Колонка | Тип | Обязательное | Описание |
|---------|-----|--------------|----------|
| `id` | INTEGER | ✅ | Уникальный ID (PK) |
| `operation_date` | TEXT | ✅ | Дата операции |
| `cashbox_code` | TEXT | ✅ | Код кассы |
| `operation_type` | TEXT | ✅ | Тип операции |
| `direction` | TEXT | ✅ | `INCOME` или `EXPENSE` |
| `amount` | REAL | ✅ | Сумма |
| `period_code` | TEXT | ❌ | Период |
| `apartment_number` | TEXT | ❌ | Квартира |
| `vehicle_id` | INTEGER | ❌ | Ссылка на автомобиль |
| `service_code` | TEXT | ❌ | Код услуги |
| `payment_id` | INTEGER | ❌ | Ссылка на платёж |
| `operator_id` | TEXT | ❌ | ID оператора |
| `comment` | TEXT | ❌ | Комментарий |

**Связи:** `cashbox_operations.cashbox_code` → `cashboxes.cashbox_code`

---

## 🔄 Также обнови раздел `service_catalog`

