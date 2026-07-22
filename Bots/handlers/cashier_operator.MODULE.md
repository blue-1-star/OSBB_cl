# MODULE: cashier_operator

Status: draft
Handler: `cashier_operator.py`
Created: 2026-07-02 19:49:54
Last reviewed: TODO
Code hash at creation: `5b73b4c3a0603f0a`

## Purpose

Операторский кассовый редактор ОСББ.

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
- `text`
- `now_db`
- `today`
- `money`
- `next_month`
- `normalize_period`
- `normalize_date`
- `parse_amount`
- `get_db_file`
- `get_conn`
- `table_exists`
- `table_columns`
- `q`
- `required_defaults`
- `insert_dynamic`
- `update_dynamic`
- `cashier_schema_ready`
- `user_can_manage_cashier`
- `cashbox_label`
- `list_active_cashboxes`
- `calculated_cashbox_balance`
- `recalc_and_store_cashbox_balance`
- `format_cashbox_balances`
- `unit_from_member`
- `resolve_cashier_unit`
- `receipt_number_for`
- `service_hint_label`
- `origin_label`
- `receipt_kind_label`

### Classes

- None detected.

### Database / schema keywords found

- `payments`
- `payment_allocations`
- `cashbox_operations`
- `audit_log`
- `apartments`
- `charges`

### User-facing text / buttons found

- `(остаток платежа {money(unallocated)}, `
- `)

    cashbox_code = text(data.get(`
- `CASH_RECEIVED`
- `CASH_TRANSFER`
- `FROM payment_allocations WHERE payment_id = ?`
- `PARKING_DAY`
- `PARKING_NIGHT`
- `PARKING_UNSPECIFIED`
- `SELECT amount FROM payments WHERE id = ?`
- `SELECT initial_balance FROM cashboxes WHERE cashbox_code = ?`
- `SELECT is_active FROM cashboxes WHERE cashbox_code = ?`
- `UPDATE cashboxes SET `
- `]

CASHIER_MENU = [
    [`
- `]
        for box in list_active_cashboxes():
            code = box[`
- `]
RECEIPT_CASHBOX_CODES = [`
- `]
TRANSFER_CASHBOX_CODES = [`
- `amount,source,cashbox_code`
- `cash`
- `cash_correction`
- `cash_receipt`

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

---

<!-- Added by OSBB Documentator: 2026-07-05 11:32:18 -->

## Parking Payments and parking_time Reconciliation

Date: 2026-07-05

Parking payments are not just financial records.

A confirmed parking payment can be used as strong evidence for filling missing parking registry data, especially `parking_time`.

The cashier workflow should preserve enough information for later reconciliation:

- apartment number
- payment date
- amount
- tariff/service code
- comment/source document
- cashier/operator
- source reference
- possible vehicle binding when known

Payment-based inference must be reviewed when there are conflicts:

- multiple vehicles for one apartment
- vehicle not linked to payment
- conflicting parking mode in registry
- historical/bulk payment source is ambiguous

Planned functionality:

- single payment entry
- historical payment entry
- bulk payment input from paper sheets
- reporting payments that can fill missing `parking_time`
- creating review candidates for admin/operator approval
