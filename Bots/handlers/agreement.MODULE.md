# MODULE: agreement

Status: draft
Handler: `agreement.py`
Created: 2026-07-02 19:49:54
Last reviewed: TODO
Code hash at creation: `ab816aceedb137b1`

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
- `get_skipped_apartments`
- `get_skipped_tariff_vehicles`
- `show_tariff_review_menu`
- `show_next_tariff_vehicle`
- `show_vehicle_edit_menu`
- `handle_vehicle_edit_flow`
- `handle_tariff_vehicle_action`
- `show_agreement_menu`
- `show_agreement_stats`
- `show_agreement_dashboard`
- `show_agreement_card`
- `show_next_agreement_apartment`
- `show_agreement_list`
- `handle_waiting_agreement_apartment`
- `handle_agreement_card_action`
- `handle_agreement_menu_text`

### Classes

- None detected.

### Database / schema keywords found

- `vehicles`
- `apartments`

### User-facing text / buttons found

- `Введите новый номер авто:`
- `Все доступные автомобили без тарифа в этой сессии просмотрены.`
- `Создано авто: {applied.get(`
- `⏳ Квартира {apartment_number} отложена.`
- `⚠️ Квартира {apartment_number}: конфликт.`
- `✅ Квартира {apartment_number} согласована.`
- `✅ Квартира {apartment_number} согласована.\n`
- `✅ Марка обновлена.`
- `✅ Номер обновлён.`
- `✅ Согласовать`
- `✅ Тариф обновлён: {tariff_map[text]}`
- `✅ Тариф сохранён: {tariff_map[text]}`
- `✏️ Исправить авто`
- `🏠 Найти квартиру`

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
- 2026-07-02 19:49:54 - MODULE.md created by module_registry_manager.py.
<!-- MODULE_CHANGELOG:END -->
