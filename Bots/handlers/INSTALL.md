# OSBB Vehicle Registry v0.2 — установка

Заменить файл:

```text
OSBB\Bots\handlers\vehicle_card_editor.py
```

Файл `parking_bot.py` в этой версии не меняется.

Проверка синтаксиса:

```powershell
python -m py_compile .\OSBB\Bots\handlers\vehicle_card_editor.py
python -m py_compile .\OSBB\Bots\parking_bot.py
```

После перезапуска бота:

```text
Админ-режим
→ Автомобили
→ Добавить автомобиль
```

На первом шаге доступны два варианта:

- указать квартиру;
- выбрать `Квартира неизвестна`.

При неизвестной квартире создаётся черновик автомобиля с `review_status = NEEDS_REVIEW`. Черновик виден в обычном поиске по известному фрагменту номера. После заполнения квартиры и полного номера та же запись автоматически получает `review_status = VERIFIED`; её `vehicle_id` не меняется.
