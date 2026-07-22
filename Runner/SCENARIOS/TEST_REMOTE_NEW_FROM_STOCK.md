# TEST_REMOTE_NEW_FROM_STOCK

Status: Draft v1  
Created: 2026-07-03  
Owner: OSBB  

## Goal

Verify the complete lifecycle of ordering new remotes/pults from available stock:

resident request → debt check → request creation → responsible parties notified → stock reserve → payment → transfer to issue point → issue → close.

## Business decision captured

1. Resident requests new remotes/pults.
2. Debt check is performed before request creation.
3. If debt exists, the request is not created.
4. If no debt exists, the request/intention is created.
5. Operator, cashier, and admins see the new request.
6. Stock is reserved if available.
7. Cashier accepts payment only after the request exists.
8. Paid reserved items are transferred to issue point.
9. Operator/issue point gives remotes to resident.
10. Request is closed.

## Preconditions

- Working DB only.
- Apartment: 174.
- Apartment 174 has no debt.
- Resident account is bound to apartment 174.
- Warehouse has at least 2 new remotes/pults.
- Operator/cashier/admin roles exist.
- Test does not touch production DB.

## Actors

- Resident
- System
- Operator
- Cashier
- Admin
- Warehouse
- Issue point

## Scenario steps

### Step 1 — Resident requests new remotes

Resident 174 requests 2 new remotes/pults.

Expected: debt check is triggered; no order is finalized before debt check result.

### Step 2 — Debt check

System verifies apartment 174 has no debt.

Expected: debt status is clear; scenario continues.

### Step 3 — Request is created

System creates a service request/intention.

Expected: request exists; status is created/intention/open; quantity is 2.

### Step 4 — System event and visibility

Operator, cashier, and admins can see the new request.

Expected: event/notification/log exists for responsible roles; request is visible in operator and cashier workspaces.

### Step 5 — Stock reserve

System checks warehouse stock.

Expected: at least 2 remotes are available; reserve is created for quantity 2.

### Step 6 — Cashier accepts payment

Cashier accepts payment for the request.

Expected: payment record exists; request payment status becomes paid.

### Step 7 — Transfer to issue point

Paid reserved remotes are transferred from stock reserve to issue point.

Expected: reserve becomes ready_to_issue/transferred; issue point receives quantity 2.

### Step 8 — Issue remotes

Operator/issue point gives remotes to resident.

Expected: issued quantity is 2; issued assets/records exist; audit log exists.

### Step 9 — Close request

System closes the request.

Expected: request status is closed/issued/completed; no unresolved mandatory step remains.

## DB tables to observe

- service_orders
- service_order_steps
- service_order_interests
- payments
- cashbox_operations
- notifications
- operator_audit_log
- warehouse_stock
- warehouse_reservations
- remote_order_issued_assets

## DB diff expectations

Expected:

- +1 service request/order or intention
- + role visibility/notifications
- + stock reservation for 2 remotes
- + payment
- + issue/asset records
- request status changes to completed/closed

Forbidden:

- production DB changes
- request created before debt check
- payment without existing request

## Evidence to store

- scenario passport
- executable YAML
- run log
- before snapshot JSON
- after snapshot JSON
- db diff JSON
- Markdown report

## Related module notes

```text
Bots/handlers/service_orders_workspace.MODULE.md
```
