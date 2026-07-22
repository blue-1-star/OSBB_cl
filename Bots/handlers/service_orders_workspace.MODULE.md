# MODULE: service_orders_workspace

Status: draft
Handler: `service_orders_workspace.py`
Created: 2026-07-02 19:49:55
Last reviewed: TODO
Code hash at creation: `317844c2f0d3ec21`

## Purpose

Telegram UI for simplified OSBB services.

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
- `phone_tr`
- `_phone_point_text`
- `_phone_access_summary_for_interest`
- `_phone_access_summary_for_order`
- `kb`
- `_state`
- `_home`
- `_service_back`
- `_operator_back`
- `_resident_entry_texts`
- `_operator_entry_texts`
- `_phone_entry_texts`
- `_is_phone_offer`
- `_normalise_phone`
- `_valid_phone`
- `_requested_phone`
- `_phone_offer_sort_key`
- `_resident_phone_offer`
- `_account_and_unit`
- `_category_allowed`
- `has_service_workspace_access`
- `_ensure_and_reconcile`
- `_current_offers`
- `_offer_buttons`
- `_next_waiting_step`
- `_step_lines`
- `_order_card`
- `_interest_card`
- `_phone_profile_gate`

### Classes

- None detected.

### Database / schema keywords found

- `service_orders`

### User-facing text / buttons found

- `


def _phone_offer_sort_key(offer: dict) -> tuple[int, int, str]:
    `
- `
            )
        subscription = phone_summary.get(`
- ` in profile


def _normalise_phone(value: str) -> str:
    return re.sub(r`
- ` Телефон доступу: {_requested_phone(interest)}.`
- `)
        if _requested_phone(interest):
            lines.append(f`
- `)
        if _requested_phone(order):
            lines.append(f`
- `) or phone_summary.get(`
- `),
            phone_tr(lang, `
- `,
            *(_phone_point_status_lines(points) or [`
- `,
            phone_tr(lang, `
- `,
        ]
        if phone_summary.get(`
- `,
    NEW_REMOTE_PROFILE: `
- `,
    ]
    if is_phone:
        quote = state[`
- `,
    ]
    if phone_summary:
        points = phone_summary.get(`
- `,
}


PHONE_UI: dict[str, dict[str, str]] = {
    `
- `,
}


def _phone_profile_gate(data: dict) -> dict:
    `
- `, phone):
            await update.message.reply_text(tr(lang, `
- `, text(value))


def _valid_phone(value: str) -> bool:
    return re.fullmatch(r`
- `CASH_HANDOVER`
- `Choose the one resident-facing phone offer from possibly noisy catalog data.`

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
- 2026-07-03 17:34:30 - Workflow: new remote/pult order.

Correct order:
1. Resident requests new remote x2.
2. Debt check happens before request creation.
   If debt exists, request is NOT created.
3. If no debt, system creates request/intention.
4. System event: operator, cashier, admins see the new request.
5. Stock reserve:
   - if stock has enough remotes, reserve x2;
   - if not, create supplier need.
6. Cashier accepts payment only after request exists.
7. After payment, reserved remotes move to issue point.
8. Issue point/operator gives remotes to resident.
9. Request is closed.

Important:
Payment is not the second step. Notification/visibility of the created request is second.
Stock reservation and transfer to issue point are separate workflow states.
<!-- MODULE_NOTES:END -->

## Change log

<!-- MODULE_CHANGELOG:BEGIN -->
- 2026-07-02 19:49:55 - MODULE.md created by module_registry_manager.py.
- 2026-07-03 17:34:30 - Note added.
<!-- MODULE_CHANGELOG:END -->
