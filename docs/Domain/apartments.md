# Apartment — Модель квартиры

## Назначение

Объединение жителей, автомобилей и статусов согласования.

## Поля

| Поле | Тип | Описание |
|------|-----|----------|
| `apartment_number` | str | Номер квартиры |
| `entrance` | str | Подъезд |
| `verification_status` | str | new, confirmed, deferred, conflict, in_progress |

## Свойства

| Свойство | Тип | Описание |
|----------|-----|----------|
| `display_name` | str | 105 (п. 1) |
| `residents_count` | int | Количество жителей |
| `vehicles_count` | int | Количество автомобилей |
| `has_residents` | bool | Есть ли жители |
| `has_vehicles` | bool | Есть ли автомобили |

## Методы

| Метод | Возвращает | Описание |
|-------|------------|----------|
| `get_by_number(apartment)` | `Apartment` | Получить по номеру |
| `get_all(limit)` | `List[Apartment]` | Все квартиры |
| `get_by_entrance(entrance)` | `List[Apartment]` | Квартиры подъезда |
| `format_card()` | `str` | Карточка для отображения |

## Пример

from core_new.domain.apartments import Apartment

apartment = Apartment.get_by_number("105")
print(apartment.format_card())
