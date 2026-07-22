# Remote / Pult Order Workflow

Status: draft  
Created: 2026-07-03  

## Correct business order

1. Resident requests a new remote/pult.
2. Debt check happens before request creation.
3. If debt exists, the request is not created.
4. If no debt exists, the system creates a request/intention.
5. System event is emitted: operator, cashier, and admins can see the request.
6. Stock is checked.
7. If stock is sufficient, requested quantity is reserved.
8. If stock is not sufficient, supplier need/order flow is started.
9. Cashier accepts payment only after the request exists.
10. After payment, reserved stock is transferred to issue point.
11. Operator/issue point gives remotes to resident.
12. Request is closed.

## Important architectural rule

Payment is not the second step.

The second functional step after resident action is visibility/notification of the created request, but only after debt check allows creation.

## Suggested stock states

```text
available
reserved_for_request
paid_ready_to_transfer
at_issue_point
issued
```

## Related modules

- service_orders_workspace
- service_orders_core
- service_preorders_core
- cashier_operator
- future warehouse/stock module

## Related scenarios

- TEST_REMOTE_NEW_FROM_STOCK
- TEST_REMOTE_NEW_DEBT_BLOCKED
- TEST_REMOTE_NEW_NO_STOCK_SUPPLIER
