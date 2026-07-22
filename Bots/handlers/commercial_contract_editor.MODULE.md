# MODULE: commercial_contract_editor

Status: draft
Handler: `commercial_contract_editor.py`
Created: 2026-07-02 19:49:55
Last reviewed: TODO
Code hash at creation: `6ea68caf65b0d643`

## Purpose

Операторский редактор индивидуальных договоров коммерческих помещений.

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
- `get_db_file`
- `get_conn`
- `table_exists`
- `state_for`
- `is_commercial_schema_ready`
- `label_status`
- `label_mode`
- `label_type`
- `parse_date`
- `parse_amount`
- `parse_int`
- `normalize_phone`
- `get_unit`
- `list_contracts`
- `get_contract`
- `list_items`
- `get_item`
- `list_recipients`
- `get_recipient`
- `list_phones`
- `get_phone`
- `create_contract`
- `update_contract`
- `set_contract_status`
- `create_item`
- `update_item`
- `create_recipient`

### Classes

- None detected.

### Database / schema keywords found

- `resident_accounts`
- `commercial_contracts`
- `operator_audit_log`
- `audit_log`
- `apartments`

### User-facing text / buttons found

- `


def format_phones(rows: list[dict]) -> str:
    purpose = {`
- ` else value
        try:
            update_contract(int(contract_id), `
- `)
    for i, contract in enumerate(contracts, 1):
        lines.append(f`
- `,
    ])


def phone_label(row: dict) -> str:
    return row.get(`
- `, (
            int(contract_id), normalized, display,
            `
- `, (int(contract_id),))
            debt = cur.fetchone()
            result[`
- `, (now_db(), int(contract_id)))
        cur.execute(f`
- `, user_id)
            update_contract(int(contract_id), `
- `COMMERCIAL_CONTRACT`
- `SELECT * FROM commercial_access_phones WHERE id=?`
- `SELECT {field} FROM commercial_contracts WHERE id=?`
- `SELECT {field}, contract_id FROM commercial_access_phones WHERE id=?`
- `SELECT {field}, contract_id FROM commercial_contract_items WHERE id=?`
- `SELECT {field}, contract_id FROM commercial_contract_recipients WHERE id=?`
- `UPDATE commercial_access_phones SET access_purpose=?, updated_at=? WHERE id=?`
- `UPDATE commercial_contract_items SET {field}=?, updated_at=? WHERE id=?`
- `UPDATE commercial_contracts SET status=?, updated_by=?, updated_at=? WHERE id=?`
- `UPDATE commercial_contracts SET {field}=?, updated_by=?, updated_at=? WHERE id=?`
- `]
    if not contracts:
        lines.append(`
- `access_phones`

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
