#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
runner_generator.py

Creates the OSBB Runner scaffold and the first scenario documents.

Dry run:
  python .\OSBB\tools\generators\runner_generator.py create --scenario TEST_REMOTE_NEW_FROM_STOCK

Apply:
  python .\OSBB\tools\generators\runner_generator.py create --scenario TEST_REMOTE_NEW_FROM_STOCK --apply
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path(r"G:\Programming\Py\OSBB")
DEFAULT_SCENARIO = "TEST_REMOTE_NEW_FROM_STOCK"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def norm(name: str) -> str:
    return name.strip().upper().replace("-", "_").replace(" ", "_")


def module_md() -> str:
    return f"""# MODULE: Runner

Status: draft  
Created: {today()}  

## Purpose

Runner is the OSBB business scenario execution subsystem.

It is not a unit-test framework. Runner describes, executes, journals, and verifies complete business workflows.

## Core rule

No scenario starts from Python.

Order:

1. Markdown scenario passport.
2. YAML executable scenario.
3. Runner execution.
4. DB snapshot.
5. DB diff.
6. Markdown/JSON report.

## Responsibilities

- Create disposable Working DB from a known source DB.
- Execute approved business scenario.
- Record each step.
- Verify expected database changes.
- Store useful evidence, not unnecessary full DB copies.
- Keep failed Working DB copies only when debugging requires them.

## Scenario states

Draft → Approved → Executable → Verified → Regression → Archived

## Directory map

```text
Runner/
  MODULE.md
  README.md
  KNOWLEDGE/
  SCENARIOS/
  SCENARIOS_YAML/
  REPORTS/
  DB_SNAPSHOTS/
  DB_DIFFS/
  LOGS/
```

## Notes

<!-- MODULE_NOTES:BEGIN -->
- {now()} - Runner scaffold generated.
<!-- MODULE_NOTES:END -->

## Change log

<!-- MODULE_CHANGELOG:BEGIN -->
- {now()} - Runner module created by runner_generator.py.
<!-- MODULE_CHANGELOG:END -->
"""


def readme_md() -> str:
    return f"""# OSBB Runner

Runner executes complete OSBB business workflows.

## Golden rule

Markdown first. YAML second. Python last.

## Workflow

```text
Business decision
  ↓
Scenario passport: Runner/SCENARIOS/*.md
  ↓
Executable scenario: Runner/SCENARIOS_YAML/*.yaml
  ↓
Runner execution
  ↓
DB before/after snapshots
  ↓
DB diff
  ↓
Report
```

## First scenario

```text
Runner/SCENARIOS/TEST_REMOTE_NEW_FROM_STOCK.md
Runner/SCENARIOS_YAML/TEST_REMOTE_NEW_FROM_STOCK.yaml
```

Runner v1 should prove one scenario well instead of pretending to cover everything.

Created: {today()}
"""


def knowledge_md() -> str:
    return f"""# Remote / Pult Order Workflow

Status: draft  
Created: {today()}  

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
"""


def index_md(scenario: str) -> str:
    return f"""# Runner Scenarios

Generated: {now()}

## Active

| Scenario | Status | Priority | Purpose |
|---|---|---:|---|
| `{scenario}` | Draft | ★★★★★ | New remote/pult order from stock, paid and issued |

## Planned

| Scenario | Status | Purpose |
|---|---|---|
| `TEST_REMOTE_NEW_DEBT_BLOCKED` | planned | Debt blocks request creation |
| `TEST_REMOTE_NEW_NO_STOCK_SUPPLIER` | planned | Supplier need is created when stock is missing |
| `TEST_PHONE_ACCESS_PAID` | planned | Phone access request lifecycle |
"""


def scenario_md(scenario: str) -> str:
    return f"""# {scenario}

Status: Draft v1  
Created: {today()}  
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
"""


def scenario_yaml(scenario: str) -> str:
    return f"""scenario: {scenario}
version: 1.0
status: draft

description: >
  New remote/pult order from available stock.
  Debt is checked before request creation.
  Operator/cashier/admin visibility happens before payment.
  Stock reserve and issue-point transfer are separate workflow states.

database:
  mode: working_copy
  source: osbb_test
  keep_full_db_on_success: false
  keep_full_db_on_failure: true

actors:
  resident:
    apartment: 174
  operator: default_test_operator
  cashier: default_test_cashier
  admin: default_test_admin
  warehouse: default_test_warehouse

preconditions:
  apartment:
    number: 174
    debt: false
    bound_resident: true
  stock:
    remote_new:
      available_at_start: 2

steps:
  - id: debt_check
    actor: system
    action: check_debt
    params:
      apartment: 174
    expect:
      debt: false
      request_created: false

  - id: create_request
    actor: resident
    action: order_new_remote
    params:
      apartment: 174
      quantity: 2
    expect:
      request_created: true
      quantity: 2
      status_any_of: [created, intention, open]

  - id: notify_responsible_roles
    actor: system
    action: emit_request_created_event
    expect:
      visible_to: [operator, cashier, admin]

  - id: reserve_stock
    actor: warehouse
    action: reserve_stock
    params:
      item: remote_new
      quantity: 2
    expect:
      reserved_quantity: 2

  - id: accept_payment
    actor: cashier
    action: accept_payment
    params:
      method: cash
      quantity: 2
    expect:
      payment_created: true
      payment_status: paid

  - id: transfer_to_issue_point
    actor: warehouse
    action: transfer_reserved_to_issue_point
    params:
      item: remote_new
      quantity: 2
    expect:
      issue_point_quantity: 2
      status_any_of: [ready_to_issue, transferred]

  - id: issue_remote
    actor: operator
    action: issue_remote
    params:
      quantity: 2
    expect:
      issued_quantity: 2
      audit_log: true

  - id: close_request
    actor: system
    action: close_request
    expect:
      status_any_of: [issued, completed, closed]

db_observe:
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

db_expect:
  created_or_changed:
    service_request_or_order: 1
    notifications_or_visibility: true
    warehouse_reservation_quantity: 2
    payment: true
    issued_quantity: 2
    closed_request: true
  forbidden:
    production_db_changes: true
    payment_without_request: true
    request_before_debt_check: true

reports:
  markdown: true
  json: true
  db_diff_json: true
"""


def planned_files(root: Path, scenario: str) -> dict[Path, str]:
    runner = root / "Runner"
    return {
        runner / "MODULE.md": module_md(),
        runner / "README.md": readme_md(),
        runner / "KNOWLEDGE" / "Remote_Order_Workflow.md": knowledge_md(),
        runner / "SCENARIOS" / "INDEX.md": index_md(scenario),
        runner / "SCENARIOS" / f"{scenario}.md": scenario_md(scenario),
        runner / "SCENARIOS_YAML" / f"{scenario}.yaml": scenario_yaml(scenario),
        runner / "REPORTS" / ".gitkeep": "",
        runner / "DB_SNAPSHOTS" / ".gitkeep": "",
        runner / "DB_DIFFS" / ".gitkeep": "",
        runner / "LOGS" / ".gitkeep": "",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create")
    p.add_argument("--root", default=str(DEFAULT_ROOT))
    p.add_argument("--scenario", default=DEFAULT_SCENARIO)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    scenario = norm(args.scenario)
    files = planned_files(root, scenario)

    print("=" * 100)
    print("OSBB Runner generator")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Root:", root)
    print("Scenario:", scenario)
    print("Files:", len(files))
    print("")

    todo = []
    skipped = []
    for path, content in files.items():
        if path.exists() and not args.overwrite:
            skipped.append(path)
            print("SKIP existing:", path)
        else:
            todo.append((path, content))
            print("CREATE:", path)

    if not args.apply:
        print("\nDRY RUN COMPLETED. Re-run with --apply to create files.")
        return 0

    for path, content in todo:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    print("\nAPPLY COMPLETED")
    print("Created:", len(todo))
    print("Skipped existing:", len(skipped))
    print("Open:")
    print(root / "Runner" / "SCENARIOS" / f"{scenario}.md")
    print(root / "Runner" / "SCENARIOS_YAML" / f"{scenario}.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
