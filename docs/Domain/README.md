# Доменная модель OSBB

## Сущности

| Сущность | Файл | Описание |
|----------|------|----------|
| **Vehicle** | `vehicles.md` | Автомобиль. Тарифы, привязка к квартире. |
| **Resident** | `residents.md` | Житель. Telegram-пользователь, квартира, статус верификации. |
| **Apartment** | `apartments.md` | Квартира. Объединяет жителей и автомобили. |
| **Payment** | `payments.md` | Платёж. Сумма, услуга, статус. |
| **VehicleCandidate** | `vehicle_candidate.md` | Кандидат в автомобили. Ожидает подтверждения. |

## Связи

Resident (N) ---- (1) Apartment
Apartment (1) ---- (N) Vehicle
Apartment (1) ---- (N) Payment
Vehicle (1) ---- (N) Payment
VehicleCandidate ---- (1) Payment (временная связь)

## Жизненные циклы

| Сущность | Жизненный цикл |
|----------|----------------|
| **Vehicle** | кандидат -> создан -> активен -> архивирован |
| **Resident** | новый -> подтвердил квартиру -> проверен оператором |
| **Payment** | создан -> завершён / возвращён |
| **VehicleCandidate** | PENDING -> RESOLVED / REJECTED |

## Тестирование

python tests/test_vehicle.py
python tests/test_resident.py
python tests/test_apartment.py
python tests/test_payment.py
