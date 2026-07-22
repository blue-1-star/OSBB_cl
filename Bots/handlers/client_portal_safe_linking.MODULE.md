# MODULE: client_portal_safe_linking

Status: draft
Handler: `client_portal_safe_linking.py`
Created: 2026-07-02 19:49:55
Last reviewed: TODO
Code hash at creation: `aba11216b9a8d103`

## Purpose

Клиентский кабинет ОСББ — версия для запуска.

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

- `tr`
- `kb`
- `client_menu_keyboard`
- `client_welcome_text`
- `parking_menu_keyboard`
- `remotes_menu_keyboard`
- `admin_remote_menu_keyboard`
- `now_db`
- `get_db_file`
- `get_conn`
- `text`
- `money`
- `table_exists`
- `table_columns`
- `_portal_state`
- `_legacy_state_active`
- `_unit_select_fields`
- `_account_and_unit`
- `_find_exact_physical_unit`
- `_vehicles_for_unit`
- `_format_vehicles`
- `_link_requests_ready`
- `_create_apartment_link_request`
- `_list_admin_link_requests`
- `_get_admin_link_request`
- `_review_link_request`
- `_format_admin_link_requests`
- `_format_admin_link_request_card`
- `_get_unit_by_id`
- `_allocation_amount_column`

### Classes

- None detected.

### Database / schema keywords found

- `resident_accounts`
- `vehicles`
- `remote_requests`
- `payments`
- `payment_allocations`
- `audit_log`
- `apartments`
- `charges`

### User-facing text / buttons found

- `
        )
        parking = text(row.get(`
- ` in payment_columns
                    else (`
- ` in payment_columns else `
- ` pa2 ON pa2.payment_id = p.id`
- `)


def _remote_kind_label(kind: str, lang: str) -> str:
    return tr(lang, f`
- `PARKING_DAY`
- `PARKING_NIGHT`
- `Parking Day`
- `Parking Night`
- `admin_remote_`
- `admin_remote_card`
- `admin_remote_list`
- `admin_remote_wait_note`
- `client_parking`
- `client_phone`
- `client_remotes`
- `parking`
- `parking_balance`
- `parking_charges`
- `parking_how`

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
