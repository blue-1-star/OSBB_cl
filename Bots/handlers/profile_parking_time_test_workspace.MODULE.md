# MODULE: profile_parking_time_test_workspace

Status: draft
Handler: `profile_parking_time_test_workspace.py`
Created: 2026-07-02 19:49:55
Last reviewed: TODO
Code hash at creation: `6b291a186d0367b5`

## Purpose

Admin-only no-write TEST UI for apartment 40 / missing parking_time.

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
- `_state`
- `_with_conn`
- `_candidate_card`
- `_session_card`
- `_show_home`
- `_show_session`
- `_show_queue`
- `handle_profile_parking_time_test_text`

### Classes

- None detected.

### Database / schema keywords found

- `vehicles`
- `service_orders`

### User-facing text / buttons found

- `profile_parking_time_test_ui`
- `source_parking_display`
- `⛔ TEST-доступ доступний лише оператору.`
- `✅ Підтвердити TEST без зміни даних`
- `❌ Відхилити TEST без зміни даних`
- `🏠 Главное меню`
- `🏠 Головне меню`
- `📋 TEST-очередь parking_time`
- `📋 TEST-черга parking_time`
- `🧪 TEST parking_time`
- `🧪 TEST-сесія parking_time`
- `🧪 Ізольований TEST: перевірка parking_time`
- `🧪 Відкрити TEST кв. 40`
- `🧪 Открыть TEST кв. 40`
- `🧪 Тест parking_time`

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
