# Resident — Модель жителя

## Назначение

Управление пользователями Telegram и их связью с квартирами.

## Поля

| Поле | Тип | Описание |
|------|-----|----------|
| `telegram_user_id` | int | Telegram ID |
| `first_name` | str | Имя |
| `last_name` | str | Фамилия |
| `apartment_number` | str | Номер квартиры |
| `status` | str | new, apartment_confirmed, operator_verified, blocked |
| `role` | str | resident, admin, super_admin, guard, operator |

## Свойства

| Свойство | Тип | Описание |
|----------|-----|----------|
| `display_name` | str | Иван Петров |
| `has_apartment` | bool | Привязана ли квартира |
| `status_display` | str | Проверен оператором |

## Методы

| Метод | Возвращает | Описание |
|-------|------------|----------|
| `get_by_telegram_id(id)` | `Resident` | Получить по Telegram ID |
| `get_by_apartment(apartment)` | `List[Resident]` | Все жители квартиры |
| `link_apartment(apartment)` | `tuple` | Привязать квартиру |
| `mark_verified_by_operator()` | `tuple` | Подтвердить оператором |

## Пример

from core_new.domain.residents import Resident

resident = Resident.get_by_telegram_id(12345)
print(resident.display_name)
