#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAMP = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

DOCS = {
    PROJECT_ROOT / "KNOWLEDGE" / "Parking_Payment_To_Parking_Time_Workflow.md": """
# Parking Payment to Parking Time Workflow

## Decision note

Date: 2026-07-05

Parking payment is one of the strongest practical confirmations of a resident's parking mode.

If an apartment has paid a parking tariff and the registry still has an empty `parking_time`, this is a serious data-quality gap.

The payment means that the resident accepted the tariff in practice and confirmed it with money.

## Business rule

When there is a confirmed parking payment and `parking_time` is empty, OSBB should create a candidate update for `parking_time`.

This candidate may be treated as highly reliable, but not blindly final.

Suggested confidence:

- 99% payment-based confidence
- 1% reserved for operator/admin review

## Why review is still needed

A paid parking tariff may conflict with other registry data:

- several vehicles linked to one apartment
- unclear mapping between a payment and a specific vehicle
- tariff paid for one parking mode while registry indicates another
- historical payments entered in bulk without vehicle-level precision
- apartment-level payment exists, but vehicle-level parking data is incomplete

## Proposed workflow

1. Read confirmed parking payments.
2. Detect confirmed parking payments where apartment is known and `parking_time` is empty or unknown.
3. Create a review candidate with apartment, vehicle if known, payment id, payment date, amount, inferred parking mode, confidence and conflict notes.
4. Operator/admin reviews the candidate.
5. Approved candidate updates the registry.
6. Rejected candidate remains in audit history.

## Candidate statuses

- PENDING
- APPROVED
- REJECTED
- NEEDS_CLARIFICATION

## Implementation direction

First implement this as an inspector/report, not as destructive update:

```powershell
python .\OSBB\tools\db_inspector.py parking-time-candidates
```

Later it may become:

```powershell
python .\OSBB\tools\parking_time_reconciliation.py --dry-run
python .\OSBB\tools\parking_time_reconciliation.py --apply-approved
```

## Historical payment entry

The cashier subsystem must support historical payment entry.

Paper payment sheets still need to be entered into OSBB.

The system should support both single payment entry and bulk historical payment import.

Bulk historical input must preserve source notes and allow later reconciliation with apartments, vehicles and parking mode.
""",
    PROJECT_ROOT / "Bots" / "handlers" / "cashier_operator.MODULE.md": """
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
""",
    PROJECT_ROOT / "Bots" / "handlers" / "vehicle_verification.MODULE.md": """
## parking_time Reconciliation from Payments

Date: 2026-07-05

Vehicle verification must take cashier data into account.

A confirmed parking payment may indicate that a resident has effectively confirmed a parking mode by paying for it.

Planned checks:

- vehicles with empty `parking_time`
- apartments with confirmed parking payments
- mismatch between paid tariff and registered parking mode
- apartments with more than one vehicle and unclear payment mapping
- residents who paid but have incomplete vehicle data

The system should create correction candidates instead of silently changing registry data.

Candidate sources:

- cashier payment
- historical payment import
- guard correction proposal
- resident confirmation

Final update requires admin/operator approval unless policy explicitly allows automatic confirmation.
""",
    PROJECT_ROOT / "tools" / "db_inspector.MODULE.md": """
## Planned command: parking-time-candidates

Date: 2026-07-05

`db_inspector.py` should grow a diagnostic command:

```powershell
python .\OSBB\tools\db_inspector.py parking-time-candidates
```

Purpose:

Find cases where parking payments exist but `parking_time` is empty or inconsistent.

The command should not change the database.

It should report:

- apartment
- vehicle if known
- payment id
- payment date
- amount
- source_ref
- inferred parking mode
- conflict markers
- recommended next action

This command is the diagnostic predecessor of a future reconciliation tool.
""",
}


def append_once(path: Path, block: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.exists() else f"# {path.stem}\n"
    marker = block.strip().splitlines()[0]
    if marker in old:
        return f"SKIP: {path}"
    add = f"\n\n---\n\n<!-- Added by OSBB Documentator: {STAMP} -->\n\n{block.strip()}\n"
    path.write_text(old.rstrip() + add, encoding="utf-8")
    return f"UPDATED: {path}"


def main() -> int:
    print("=" * 100)
    print("OSBB Documentator: parking payment -> parking_time workflow")
    print("=" * 100)
    print("Project root:", PROJECT_ROOT)
    print()
    for path, block in DOCS.items():
        print(append_once(path, block))
    print()
    print("Next:")
    print("git diff --stat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
