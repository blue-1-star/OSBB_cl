# MODULE: profile_verification_workspace

Status: draft
Handler: `profile_verification_workspace.py`
Created: 2026-07-02 19:49:55
Last reviewed: TODO
Code hash at creation: `131afe386eac83ed`

## Purpose

Telegram UI for resident profile verification.

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
- `_tr`
- `_state`
- `_home`
- `_account_unit`
- `_snapshot_for`
- `_fmt`
- `_parking_display`
- `_vehicle_lines`
- `_profile_card`
- `_gate_message`
- `maybe_show_profile_welcome`
- `show_profile_verification`
- `show_phone_access_block`
- `_show_parking_vehicle_select`
- `_show_operator_queue`
- `_operator_request_card`
- `_show_operator_request`
- `handle_profile_verification_text`

### Classes

- None detected.

### Database / schema keywords found

- `vehicles`
- `service_orders`

### User-facing text / buttons found

- `   Паркування: {_parking_display(vehicle.get(`
- `   Потрібно явно підтвердити: автомобіля немає, або подати уточнення.`
- `)} передано оператору.`
- `)} передано оператору. `
- `PARKING_TIME`
- `Used by the services workspace before it displays phone-access offers.`
- `parking_time`
- `parking_time_choice`
- `parking_vehicle_buttons`
- `parking_vehicle_select`
- `phone_access_allowed`
- `Заявка надійде оператору; дані не змінюються автоматично.`
- `Оберіть автомобіль кнопкою.`
- `Поки оператор не завершить перевірку, обов’язкові дані `
- `Телефонний доступ можна підключати.`
- `вважаються незавершеними і телефонний доступ недоступний.`
- `квартиру, автомобілі, державні номери та тариф паркування.\n\n`
- `підключення телефонного доступу недоступне.`
- `⛔ Підключення телефонного доступу неможливе.`
- `✅ Approve as operator`

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
