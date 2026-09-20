# Словарь данных OSBB (Data Dictionary)

**Версия:** 1.1
**Дата обновления:** 2026-09-19
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
| `source` | TEXT | ❌ | Краткий источник записи, например `tbot_parking` |
| `created_source` | TEXT | ❌ | Читаемая цепочка происхождения, например `parking_tbot2.xlsx → quarantine (batch #1)` |
| `review_status` | TEXT | ❌ | Статус проверки; `PENDING_RESIDENT_CONFIRMATION` означает, что запись ждёт подтверждения жильцом |
| `notes` | TEXT | ❌ | Служебная заметка, в том числе номер партии и строки карантина |
| `created_at` | TEXT | ❌ | Дата и время добавления записи |

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

## 10. Таблица `data_import_batches`

**Назначение:** Журнал контролируемых партий доливки данных в основную БД. Одна строка — одна завершённая или планируемая партия, а не одна машина.

| Колонка | Тип | Обязательное | Описание |
|---------|-----|--------------|----------|
| `id` | INTEGER | ✅ | Идентификатор партии (PK) |
| `entity_type` | TEXT | ✅ | Тип данных, сейчас `vehicles` |
| `source_file_name` | TEXT | ✅ | Имя исходного файла, например `parking_tbot2.xlsx` |
| `source_file_path` | TEXT | ✅ | Путь к использованному файлу на момент импорта |
| `source_file_sha256` | TEXT | ✅ | SHA-256 файла; отличает разные версии даже при совпадающем имени |
| `pipeline` | TEXT | ✅ | Путь данных, например `parking_tbot2.xlsx → quarantine → main vehicles` |
| `source_records_count` | INTEGER | ✅ | Сколько строк было в снимке карантина |
| `selected_records_count` | INTEGER | ✅ | Сколько строк оператор отобрал в партию |
| `inserted_records_count` | INTEGER | ✅ | Сколько строк фактически добавлено |
| `skipped_records_count` | INTEGER | ✅ | Сколько строк пропущено при выполнении |
| `status` | TEXT | ✅ | Состояние партии: `APPLYING`, `APPLIED`, в будущем возможны `DRY_RUN`, `FAILED` |
| `created_at` | TEXT | ✅ | Создание партии |
| `applied_at` | TEXT | ❌ | Фактическое время применения |
| `applied_by` | TEXT | ❌ | Скрипт или оператор, применивший партию |
| `notes` | TEXT | ❌ | Причина и границы партии |

**Связи:** `data_import_batches.id` → `vehicle_import_batch_items.batch_id`.

---

## 11. Таблица `vehicle_import_batch_items`

**Назначение:** Состав партии импорта автомобилей. Сохраняет связь добавленного автомобиля с конкретной строкой карантина и её полным снимком на момент решения.

| Колонка | Тип | Обязательное | Описание |
|---------|-----|--------------|----------|
| `id` | INTEGER | ✅ | Идентификатор строки партии (PK) |
| `batch_id` | INTEGER | ✅ | Ссылка на `data_import_batches.id` |
| `source_db` | TEXT | ✅ | Файл карантинной БД |
| `source_table` | TEXT | ✅ | Таблица источника, сейчас `tbot_parking_import` |
| `source_record_id` | TEXT | ✅ | ID исходной строки карантина |
| `apartment_number` | TEXT | ❌ | Квартира из источника |
| `license_plate_raw` | TEXT | ❌ | Номер как был введён в источнике |
| `license_plate_normalized` | TEXT | ❌ | Нормализованный номер для сверки |
| `source_payload_json` | TEXT | ✅ | Снимок всех полей исходной строки в JSON |
| `decision` | TEXT | ✅ | Решение по строке, например `APPLIED` |
| `decision_reason` | TEXT | ❌ | Основание решения оператора |
| `vehicle_id` | INTEGER | ❌ | Созданный автомобиль в `vehicles` |
| `created_at` | TEXT | ✅ | Время фиксации решения |

**Ограничение:** одна строка карантина не может попасть в одну и ту же партию дважды.

---

## 12. Карантинная БД `osbb_quarantine.db`: снимки файлов TBot

Карантинная БД не является рабочим реестром. Она хранит исходные данные до решения оператора.

- `source_files` хранит файл-источник, его SHA-256, исходное имя, дату и число строк;
- `tbot_parking_import.source_file_id` связывает каждую строку с конкретным снимком файла;
- новый импорт **не очищает** прежние строки: `parking_tbot3.xlsx` должен стать новым снимком, а не заменить `parking_tbot2.xlsx`.

Обычная цепочка: **файл Excel → карантинный снимок → сухой прогон → выбранная партия основной БД → `audit_log`**.

---

## 13. Таблица `verification_tasks`: очередь верификации автомобилей

Это существующая общая очередь проверки полей реестра. Для конфликтов из TBot используется тип задачи `new_vehicle_for_populated_apartment`: файл предлагает новый автомобиль для квартиры, в которой автомобили уже зарегистрированы.

| Поле | Назначение в этом сценарии |
|------|-----------------------------|
| `task_group` | `vehicle` |
| `task_type` | `new_vehicle_for_populated_apartment` |
| `status` | `new` → `in_progress` → `resolved` |
| `apartment_id`, `apartment_number` | Квартира, по которой возник конфликт |
| `source_name`, `source_record_id` | `tbot_parking` и точная строка карантина |
| `main_value` | Уже зарегистрированные автомобили квартиры на момент создания задачи |
| `candidate_value`, `normalized_candidate_value` | Новый номер из источника и его нормализованное значение |
| `suggestion`, `comment` | Подсказка и исходные марка/цвет/ФИО для проверки |
| `import_batch_id` | Ссылка на партию в `data_import_batches`; добавлено 2026-09-19 |
| `resolution`, `resolution_comment`, `resolved_at`, `resolved_by` | Решение оператора и его обоснование |

Оператор может подтвердить кандидата с созданием записи в `vehicles` либо отклонить его. Оба действия записываются в `audit_log`.

---

## 14. Таблица `video_plate_evidence`: агрегированное доказательство из видео

Служебный кэш наблюдений номеров из Excel-результатов распознавания парковки. Это **не** реестр автомобилей и он никогда не меняет `vehicles` автоматически. Страница операторской верификации использует кэш только как подсказку с происхождением и уровнем согласия.

| Поле | Назначение |
|------|------------|
| `plate_normalized` | Нормализованный стандартный номер (PK) |
| `display_plate` | Наиболее частое исходное написание номера в видео |
| `consensus_model` | Наиболее частая модель; пусто при равном конфликте или единственном наблюдении |
| `matching_observations` | Сколько раз встретилась выбранная модель |
| `model_observations` | Сколько всего наблюдений номера содержали модель |
| `model_agreement` | Доля выбранной модели среди наблюдений с моделью |
| `evidence_status` | `CONSENSUS`, `DOMINANT_REVIEW`, `CONFLICT`, `ONE_OBSERVATION`, `NO_MODEL` |
| `observation_count` | Все появления номера в распознанных видео |
| `first_seen_date`, `last_seen_date` | Границы наблюдаемого периода |
| `source_files_json` | Имена исходных Excel-файлов, где номер встречался |
| `registry_vehicle_id`, `registry_model` | Точная запись и модель из `vehicles` при совпадении номера; второй источник подсказки, не изменение видео |
| `registry_model_relation` | Связь моделей: `MATCH`, `DIFFERENT`, `SUPPLEMENT_VIDEO`, `NO_REGISTRY_MODEL` |
| `source_signature`, `imported_at`, `algorithm_version` | Версия и момент обновления кэша |

Близкие номера ищутся по расстоянию Левенштейна не более двух операций. Совпадение номера и модель из видео остаются операторской подсказкой, а не основанием для автоматической правки.

Происхождение данных, правила нормализации, статусы согласия и безопасная
команда обновления описаны в [справке по видео-распознаванию](../Domain/video_recognition.md).

---

## 15. Таблица `video_recognition_imports`: журнал обновлений видео-кэша

Фиксирует каждую загрузку набора Excel-результатов распознавания: подпись набора файлов, количество источников, наблюдений и уникальных номеров, время, исполнитель и версию алгоритма.

---

## 16. Таблица `operator_task_queue`: общая очередь предложений и задач оператору

Очередь связывает бот самообслуживания и будущую админ-консоль. Житель не
меняет реестр напрямую: бот создаёт задачу со статусом `PENDING`, а оператор
рассматривает её в одном общем рабочем месте.

| Поле | Назначение |
|---|---|
| `task_type`, `status`, `priority` | Тип предложения, этап обработки и приоритет |
| `apartment_number`, `vehicle_id`, `plate` | Контекст квартиры и автомобиля; позволяет открыть карточку без поиска по тексту |
| `telegram_user_id`, `origin`, `created_by` | Кто предложил изменение и откуда оно пришло |
| `title`, `description` | Читаемая оператором формулировка; сохраняется также для старых задач |
| `payload_json` | Структурированное предложение `current → proposed`; добавлено миграцией `2026-09-20_012` для новых заявок из кабинета жителя |
| `assigned_to`, `closed_at`, `close_note` | Взятие в работу и результат рассмотрения |

Для заявки о проданном автомобиле бот создаёт тип
`RESIDENT_VEHICLE_PARKING_END`; в `payload_json` сохраняются дата последнего
дня парковки и причина (`SOLD` либо `NO_LONGER_PARKS`). Это ещё не прекращает
начисления: оператор должен подтвердить событие после появления модели
периодов парковки и подневного расчёта.

---

## 🔄 Также обнови раздел `service_catalog`
