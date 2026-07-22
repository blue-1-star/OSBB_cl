# MODULE: audit_viewer

Status: draft
Handler: `audit_viewer.py`
Created: 2026-07-02 19:49:54
Last reviewed: TODO
Code hash at creation: `04f0d52f7d01bcbe`

## Purpose

Постраничный просмотр основного журнала аудита ОСББ.

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
- `_ensure_state`
- `table_exists`
- `safe`
- `count_audit`
- `load_audit_page`
- `format_audit_row`
- `title_for_filter`
- `page_keyboard`
- `format_audit_page`
- `show_audit_dashboard`
- `show_audit_page`
- `handle_audit_viewer_text`

### Classes

- None detected.

### Database / schema keywords found

- `operator_audit_log`
- `audit_log`

### User-facing text / buttons found

- `Правки операторов`
- `🏠 Главное меню`
- `👤 Правки операторов`

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
