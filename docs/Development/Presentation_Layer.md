<!-- BEGIN: DEVELOPMENT-DOCS-V1 -->
# Presentation Layer

## Назначение

Presentation Layer преобразует состояние системы в понятное представление для
пользователя и не содержит бизнес-логику.

```text
Business Logic
      ↓
State / Result
      ↓
Presentation Layer
      ↓
Telegram / Web / Desktop / Assistant
```

## UI-020

После успешной операции пользователь получает не абстрактное сообщение об
успехе, а актуальное состояние кассы или другого затронутого объекта.

Первый объект:

```python
render_cashier_state(...)
```

Планируемое семейство:

```python
render_cashier_state(...)
render_apartment_state(...)
render_vehicle_state(...)
render_resident_state(...)
render_service_state(...)
render_payment_state(...)
render_finance_state(...)
```

## Принципы

- бизнес-логика возвращает состояние, а не Telegram-текст;
- форматирование отделено от изменения данных;
- один объект состояния отображается в разных клиентах;
- Telegram — первый клиент, но не центр архитектуры.
<!-- END: DEVELOPMENT-DOCS-V1 -->
