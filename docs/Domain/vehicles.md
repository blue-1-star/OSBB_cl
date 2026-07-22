# Vehicle — Модель автомобиля

## Назначение

Управление автомобилями в системе.

## Поля

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | int | Уникальный ID |
| `apartment_number` | str | Номер квартиры |
| `plate` | str | Номер автомобиля |
| `model` | str | Марка автомобиля |
| `parking_time` | str | Тариф: Day, Night, Inactive, NULL |

## Свойства

| Свойство | Тип | Описание |
|----------|-----|----------|
| `display_name` | str | AA8098MM | Toyota |
| `status_display` | str | День / Ночь / Не паркуется |
| `is_parking` | bool | Паркуется ли (Day или Night) |
| `is_active` | bool | Активен ли (не Inactive) |

## Методы

| Метод | Возвращает | Описание |
|-------|------------|----------|
| `get_by_id(id)` | `Vehicle` | Получить автомобиль по ID |
| `get_by_apartment(apartment)` | `List[Vehicle]` | Все авто квартиры |
| `set_parking_time(status)` | `tuple` | Установить тариф |

## Пример

from core_new.domain.vehicles import Vehicle

vehicles = Vehicle.get_by_apartment("105")
for v in vehicles:
    print(f"{v.display_name}: {v.status_display}")
