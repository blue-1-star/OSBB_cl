# MODULE: vehicle_card_editor

Status: draft
Handler: `vehicle_card_editor.py`
Created: 2026-07-02 19:49:55
Last reviewed: TODO
Code hash at creation: `f600b3467ac00f29`

## Purpose

TODO: describe module purpose.

## Business meaning

TODO: describe what real OSBB process this module supports.

Examples:

- resident self-service;
- operator workspace;
- payments/cashbox;
- remote/pult requests;
- phone access;
- vehicle registry;
- commercial contracts;
- guard workspace.

## Current implementation evidence

### Functions

- `kb`
- `get_db_file`
- `get_conn`
- `normalize_plate`
- `is_valid_ua_plate`
- `norm_apartment`
- `table_columns`
- `get_apartment_id`
- `search_vehicles`
- `get_vehicle`
- `format_vehicle_line`
- `format_search_results`
- `format_vehicle_card`
- `update_vehicle_field`
- `update_vehicle_plate`
- `update_vehicle_model`
- `update_vehicle_apartment`
- `show_vehicle_search_start`
- `show_vehicle_card`
- `handle_search_query`
- `ask_edit_value`
- `apply_edit_value`
- `handle_vehicle_card_editor_text`

### Classes

- None detected.

### Database / schema keywords found

- `vehicles`
- `audit_log`
- `apartments`

### User-facing text / buttons found

- `) AS model,
            COALESCE(v.parking_time, `
- `parking_time`
- `Автомобиль не найден.`
- `Введите номер авто, номер квартиры или марку.\n`
- `Исправление марки автомобиля через карточку авто`
- `Исправление номера автомобиля через карточку авто`
- `Карточка авто не найдена.`
- `Квартира не найдена: {new_apt}`
- `Можно искать по номеру авто, квартире или марке.\n`
- `Перенос автомобиля в другую квартиру через карточку авто`
- `Редактирование карточки автомобиля в боте`
- `✅ Изменение сохранено\n\n`
- `✏️ Марка авто\n\nТекущая марка: {vehicle.get(`
- `✏️ Номер авто\n\nТекущий номер: {vehicle.get(`
- `🏠 Главное меню`
- `🏠 Квартира`
- `🏠 Квартира\n\nТекущая квартира: {vehicle.get(`
- `🔎 Найдены автомобили:`
- `🔎 Найти авто`
- `🔎 Найти авто\n\n`

## Dependencies

TODO: list related handlers, services, tables, migrations.

## Entry points

TODO: list where this module is called from.

## Known legacy / risks

TODO: record confusing old flows, duplicates, copy-files, partial implementations.

## Acceptance tests

- [ ] Opens without error.
- [ ] Main menu path is known.
- [ ] Creates expected DB records.
- [ ] Operator/admin can see created records.
- [ ] Status change works.
- [ ] Audit trail is written where needed.
- [ ] Tested on Working DB, not Golden Master.

## Current notes

<!-- MODULE_NOTES:BEGIN -->
<!-- MODULE_NOTES:END -->

## Change log

<!-- MODULE_CHANGELOG:BEGIN -->
- 2026-07-02 19:49:55 - MODULE.md created by module_registry_manager.py.
<!-- MODULE_CHANGELOG:END -->
