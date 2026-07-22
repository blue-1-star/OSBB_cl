# MODULE: guard_workspace

Status: draft
Handler: `guard_workspace.py`
Created: 2026-07-02 19:49:55
Last reviewed: TODO
Code hash at creation: `3b3ad3677ca866de`

## Purpose

Отдельный рабочий кабинет охранника поста / кассы O.

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

- `now_db`
- `kb`
- `_state`
- `_global_switch_text`
- `_is_allowed`
- `_denied`
- `_guard_ready`
- `show_guard_workspace`
- `_cash_notices_o`
- `_show_cash_notices`
- `_show_notice_card`
- `_confirm_notice`
- `_service_buttons`
- `_ask_manual_unit`
- `_select_cash_unit`
- `_show_manual_cash_preview`
- `_save_manual_cash`
- `_remote_gate_allocation_amount_column`
- `_remote_gate_service_is_blocking`
- `_remote_gate_block_message`
- `_remote_gate_debt_for_apartment`
- `_remote_rows_for_issue`
- `_insert_remote_event`
- `_start_remote_received`
- `_select_remote_received_unit`
- `_save_remote_received`
- `_show_remote_issue_list`
- `_remote_request`
- `_show_remote_issue_card`
- `_save_remote_issued`

### Classes

- None detected.

### Database / schema keywords found

- `remote_requests`
- `payments`
- `payment_allocations`
- `cashbox_operations`
- `operator_audit_log`
- `audit_log`
- `charges`

### User-facing text / buttons found

- `
CASHBOX_CODE = `
- `) != CASHBOX_CODE
    ):
        await update.message.reply_text(
            `
- `) != CASHBOX_CODE:
        await update.message.reply_text(`
- `: _remote_gate_block_message(`
- `: _remote_gate_block_message(apt, 0.0, `
- `CASHBOX`
- `CASH_HANDOVER`
- `LEFT JOIN payment_allocations pa ON pa.charge_id = c.id`
- `PARKING`
- `SELECT * FROM remote_requests WHERE id = ?`
- `UPDATE remote_requests SET {assignments} WHERE id = ?`
- `]

    if cash_ok:
        lines.append(`
- `]}; payment={result[`
- `cashbox_operations`
- `cashier_receipts`
- `declared_cashbox_code`
- `guard_o_cash_notice_confirmed`
- `guard_o_cash_receipt_created`
- `guard_o_remote_issued`
- `guard_o_remote_received`

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
