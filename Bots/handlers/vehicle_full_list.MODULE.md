# MODULE: vehicle_full_list

Status: draft
Handler: `vehicle_full_list.py`
Created: 2026-07-02 19:49:55
Last reviewed: TODO
Code hash at creation: `6ef0a2405d66d14b`

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
- `get_all_vehicles_full`
- `format_vehicle_line`
- `build_full_vehicle_messages`
- `send_full_vehicle_list`
- `handle_vehicle_full_list_text`

### Classes

- None detected.

### Database / schema keywords found

- `vehicles`
- `apartments`

### User-facing text / buttons found

- `) AS model,
            COALESCE(v.parking_time, `
- `🏠 Главное меню`
- `📋 Все автомобили`
- `🔎 Найти авто`
- `🚗 Все автомобили`
- `🚗 Все автомобили\n\nСписок пуст.`

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
