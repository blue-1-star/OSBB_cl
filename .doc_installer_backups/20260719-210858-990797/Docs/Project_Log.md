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
