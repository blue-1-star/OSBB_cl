# Changelog — core_new

## 2026-07-14 — Vehicle Candidate (новая сущность)

### Что сделано

Создана новая сущность `vehicle_candidates` для обработки автомобилей без квартиры.

**Причина:**
- Касса не могла принять оплату за автомобиль без квартиры (NOT NULL constraint).
- Оператору нужно принимать оплату на месте, даже если житель не помнит номер квартиры.

**Решение:**
- Создана таблица `vehicle_candidates`.
- Создана модель `VehicleCandidate` в `core_new/domain/`.
- Касса создаёт кандидата вместо прямого INSERT в `vehicles`.
- Платеж привязывается к `candidate_id`.

**Статусы кандидата:**
- `PENDING` — создан, ждёт обработки.
- `RESOLVED` — подтверждён, создан автомобиль в `vehicles`.
- `REJECTED` — отклонён.

---

## 2026-07-13 — Реализация нового ядра core_new

### Что сделано

#### 1. Создание слоя core_new

Создан новый архитектурный слой `core_new/` для отделения бизнес-логики от интерфейсов.

#### 2. Реализация адаптера

Создан `DBAdapter` — обёртка над `Bots/db_access.py`.

#### 3. Доменные модели

| Модель | Файл | Статус |
|--------|------|--------|
| `Vehicle` | `domain/vehicles.py` | ✅ |
| `Resident` | `domain/residents.py` | ✅ |
| `Apartment` | `domain/apartments.py` | ✅ |
| `Payment` | `domain/payments.py` | ✅ |
| `VehicleCandidate` | `domain/vehicle_candidate.py` | ✅ |

#### 4. Тесты

Созданы тесты для всех моделей.

#### 5. Документация

- `ADR-2026-07-13-core-new-layer.md`
- `ADR-2026-07-13-domain-models.md`
- `Domain/README.md`
- `Domain/vehicles.md`, `residents.md`, `apartments.md`, `payments.md`
- `Code/core_new.md`
