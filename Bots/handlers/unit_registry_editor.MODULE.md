# MODULE: unit_registry_editor

Status: draft
Handler: `unit_registry_editor.py`
Created: 2026-07-02 19:49:55
Last reviewed: TODO
Code hash at creation: `531d8ddba81a51fc`

## Purpose

Операторский редактор реестра помещений.

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

- `unit_card_menu`
- `kb`
- `_is_registry_state`
- `_registry_state`
- `now_db`
- `get_db_file`
- `get_conn`
- `q`
- `text`
- `table_exists`
- `table_columns`
- `schema_ready`
- `operator_can_edit`
- `normalize_code`
- `unit_kind_label`
- `status_label`
- `get_primary_contact`
- `get_unit_by_id`
- `list_units`
- `get_unit_by_code`
- `format_unit_list`
- `list_buttons`
- `format_unit_card`
- `update_unit_field`
- `update_primary_contact`
- `create_new_unit`
- `show_registry_menu`
- `show_unit_list`
- `show_unit_card`
- `start_create_unit`

### Classes

- None detected.

### Database / schema keywords found

- `operator_audit_log`
- `audit_log`
- `apartments`

### User-facing text / buttons found

- `contact_phone`
- `Введите телефон или «-» чтобы очистить.`
- `Здесь оператор ведёт коммерческие и технические помещения.\n`
- `Изменение основного контакта помещения оператором.`
- `Редактирование карточки помещения оператором.`
- `Существование и официальный номер подтверждены оператором. `
- `Требуется уточнить площадь, контакты и договорные условия.`
- `Этот раздел доступен оператору или администратору.`
- `✅ Подтвердить`
- `✅ Статус изменён\n\n`
- `✅ Черновик помещения создан.`
- `✏️ Телефон`
- `✏️ Телефон\n\n`
- `🏠 Главное меню`
- `📄 Договоры`
- `📋 К списку`

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
