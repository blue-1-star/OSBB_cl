# VehicleCandidate — Кандидат в автомобили

## Назначение

Хранит информацию об автомобилях, которые ещё не подтверждены и не привязаны к квартире.

## Поля

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | int | Уникальный ID |
| `license_plate` | str | Номер автомобиля |
| `car_model` | str | Марка и модель |
| `apartment_number` | str | Номер квартиры (может быть NULL) |
| `status` | str | PENDING, RESOLVED, REJECTED, MERGED |
| `created_by` | int | Telegram ID создателя |
| `source` | str | cashier, telegram, tbot |

## Жизненный цикл

PENDING -> RESOLVED -> vehicle created in vehicles
PENDING -> REJECTED

## Методы

| Метод | Возвращает | Описание |
|-------|------------|----------|
| `create(plate, model, apartment, created_by)` | `VehicleCandidate` | Создать кандидата |
| `resolve(apartment_number)` | `tuple` | Подтвердить -> создать автомобиль |
| `reject(reason)` | `tuple` | Отклонить кандидата |

## Пример

from core_new.domain.vehicle_candidate import VehicleCandidate

candidate = VehicleCandidate.create(
    plate="AA9999XX",
    model="Tesla",
    apartment_number=None,
    created_by=12345
)

success, vehicle = candidate.resolve("105")
