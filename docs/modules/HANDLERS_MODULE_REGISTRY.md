# OSBB Handlers Module Registry

Generated: 2026-07-02 19:52:21

Handlers directory: `G:\Programming\Py\OSBB\Bots\handlers`

| Module | Handler | MODULE.md | Status | Size KB | Modified | Functions | DB keywords |
|---|---|---|---|---:|---|---:|---|
| `__init___` | `__init___.py` | `__init___.MODULE.md` | documented | 0 | 2026-06-14 21:01:57 | 0 |  |
| `agreement` | `agreement.py` | `agreement.MODULE.md` | documented | 17 | 2026-06-17 12:09:08 | 17 | vehicles, apartments |
| `audit_viewer` | `audit_viewer.py` | `audit_viewer.MODULE.md` | documented | 10 | 2026-06-24 21:56:50 | 15 | operator_audit_log, audit_log |
| `cashier_operator` | `cashier_operator.py` | `cashier_operator.MODULE.md` | documented | 89 | 2026-06-25 14:39:13 | 61 | payments, payment_allocations, cashbox_operations, audit_log, apartments, charges |
| `client_portal` | `client_portal.py` | `client_portal.MODULE.md` | documented | 95 | 2026-06-28 17:41:44 | 62 | resident_accounts, vehicles, remote_requests, payments, payment_allocations, audit_log |
| `client_portal_safe_linking` | `client_portal_safe_linking.py` | `client_portal_safe_linking.MODULE.md` | documented | 90 | 2026-06-25 10:58:36 | 59 | resident_accounts, vehicles, remote_requests, payments, payment_allocations, audit_log |
| `commercial_contract_editor` | `commercial_contract_editor.py` | `commercial_contract_editor.MODULE.md` | documented | 75 | 2026-06-24 12:58:23 | 55 | resident_accounts, commercial_contracts, operator_audit_log, audit_log, apartments |
| `guard_workspace` | `guard_workspace.py` | `guard_workspace.MODULE.md` | documented | 46 | 2026-06-28 17:41:44 | 33 | remote_requests, payments, payment_allocations, cashbox_operations, operator_audit_log, audit_log |
| `profile_parking_time_test_workspace` | `profile_parking_time_test_workspace.py` | `profile_parking_time_test_workspace.MODULE.md` | documented | 12 | 2026-06-28 12:11:31 | 9 | vehicles, service_orders |
| `profile_verification_workspace` | `profile_verification_workspace.py` | `profile_verification_workspace.MODULE.md` | documented | 29 | 2026-06-27 21:55:14 | 19 | vehicles, service_orders |
| `service_orders_workspace` | `service_orders_workspace.py` | `service_orders_workspace.MODULE.md` | documented | 81 | 2026-06-29 17:34:37 | 58 | service_orders |
| `unit_registry_editor` | `unit_registry_editor.py` | `unit_registry_editor.MODULE.md` | documented | 36 | 2026-06-25 09:28:35 | 35 | operator_audit_log, audit_log, apartments |
| `vehicle_card_editor` | `vehicle_card_editor.py` | `vehicle_card_editor.MODULE.md` | documented | 18 | 2026-06-21 14:44:28 | 23 | vehicles, audit_log, apartments |
| `vehicle_full_list` | `vehicle_full_list.py` | `vehicle_full_list.MODULE.md` | documented | 3 | 2026-06-21 15:42:54 | 8 | vehicles, apartments |
| `vehicle_verification` | `vehicle_verification.py` | `vehicle_verification.MODULE.md` | documented | 18 | 2026-06-20 16:39:02 | 28 | vehicles, payments, operator_audit_log, audit_log, apartments |

## Operating rule

Every meaningful handler should have a companion `*.MODULE.md` file.

When a decision, defect, test result, or important observation appears during work, add it immediately:

```powershell
python .\OSBB	ools\module_registry_manager.py note --module client_portal --text "..." --apply
```
