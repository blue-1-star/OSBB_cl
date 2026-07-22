# Parking_Payment_To_Parking_Time_Workflow

---

<!-- Added by OSBB Documentator: 2026-07-05 11:32:18 -->

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
python .\OSBB	ools\db_inspector.py parking-time-candidates
```

Later it may become:

```powershell
python .\OSBB	ools\parking_time_reconciliation.py --dry-run
python .\OSBB	ools\parking_time_reconciliation.py --apply-approved
```

## Historical payment entry

The cashier subsystem must support historical payment entry.

Paper payment sheets still need to be entered into OSBB.

The system should support both single payment entry and bulk historical payment import.

Bulk historical input must preserve source notes and allow later reconciliation with apartments, vehicles and parking mode.
