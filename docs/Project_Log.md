# Project Log

## 2026-07-15

### ✅ Что сделано
- Касса (`Cashbox.register_payment`) прошла тест: создан кандидат и платёж.
- Исправлен `service_catalog` (добавлены категории COMMERCIAL/FUNDRAISING).
- Проведена миграция БД: разрешён NULL в `payments.apartment_number`.

### 📝 Обновлены документы
- [ADR-2026-07-14-vehicle-candidate.md](Architecture/ADR-2026-07-14-vehicle-candidate.md)
- [Data Dictionary — vehicle_candidates](Database/Data_Dictionary.md#6-таблица-vehicle_candidates)
- [Changelog](Changelog/CHANGELOG_core_new.md)

### 🆕 Новые сущности
- **Таблица `vehicle_candidates`** — для хранения "недоавтомобилей".
- **Модель `VehicleCandidate`** — в `core_new/domain/vehicle_candidate.py`.
- **Таблица `service_items`** — теперь используется для получения тарифов.

### 📌 Следующие шаги
- Упростить UX кассы (подстановка суммы из `service_items`).
- Создать интерфейс для подтверждения кандидатов оператором.

---

<!-- OSBB-DOCS:BEGIN finance-core-decision-2026-07-19 -->
## 2026-07-19 — Finance Core architectural decision

A cashier case with a 700 UAH receipt (500 UAH known purpose and 200 UAH
unknown purpose) exposed a structural limitation: the system should not require
all received money to be classified by service immediately.

Decision: create the `finance_core` domain. Receipt/expense facts are recorded
first; allocations, reallocations, advances, refunds, and corrections are
recorded separately with a complete audit trail.

Created architecture documents:

- `Docs/Architecture/Finance_Core.md`
- `Docs/Architecture/ADR/ADR-2026-07-19-Finance-Core.md`
<!-- OSBB-DOCS:END finance-core-decision-2026-07-19 -->

<!-- BEGIN: DEVELOPMENT-DOCS-V1 -->
## 2026-07-20 — Development Docs v1

Создан пакет инженерной документации `Docs/Development`.

Зафиксированы выводы из неудачных попыток запуска Assistant: ручной поиск причин
должен быть заменен встроенной диагностикой.

Принят принцип: каждая дорогая по времени ошибка должна превращаться в документ,
проверку, тест или функцию Assistant.
<!-- END: DEVELOPMENT-DOCS-V1 -->
