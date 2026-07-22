# Payment — Модель платежа

## Назначение

Управление финансовыми операциями.

## Поля

| Поле | Тип | Описание |
|------|-----|----------|
| `amount` | float | Сумма |
| `currency` | str | Валюта (UAH) |
| `apartment_number` | str | Номер квартиры |
| `service_code` | str | Код услуги |
| `status` | str | pending, completed, failed, refunded |
| `payment_method` | str | cash, card, bank |

## Свойства

| Свойство | Тип | Описание |
|----------|-----|----------|
| `amount_display` | str | 150.00 UAH |
| `status_display` | str | Завершен |
| `service_display` | str | Парковка |
| `is_completed` | bool | Завершён ли |

## Методы

| Метод | Возвращает | Описание |
|-------|------------|----------|
| `get_by_id(id)` | `Payment` | Получить по ID |
| `get_by_apartment(apartment)` | `List[Payment]` | Платежи квартиры |
| `create(...)` | `tuple` | Создать платёж |
| `format_card()` | `str` | Карточка платежа |

## Пример

from core_new.domain.payments import Payment

payment = Payment.create(
    amount=150.00,
    apartment_number="105",
    service_code="PARKING_DAY",
    payment_method="cash"
)
