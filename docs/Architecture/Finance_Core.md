# Finance Core

## Status

Architectural direction accepted for OSBB.

## Purpose

`finance_core` separates the movement of money from its later accounting
classification by service, period, resident, vehicle, or other analytical
attribute.

The central rule is:

> Money exists before its accounting classification.

A receipt must therefore be recorded even when its full purpose is not yet
known. Classification can happen later without rewriting the historical fact
of receipt.

## Example

A cashier receives 700 UAH:

- 500 UAH is allocated to overnight parking;
- 200 UAH remains unallocated;
- later the remaining 200 UAH may be allocated to another service, retained as
  an advance, refunded, or corrected.

The receipt remains one historical event. Allocations are separate events.

## Responsibilities

`finance_core` is responsible for:

- receipts;
- expenses;
- allocations;
- reallocations;
- advances;
- refunds;
- corrections;
- audit history.

## Boundary with service accounting

Service modules answer what a resident owes and why. `finance_core` answers
what money moved, when it moved, and how that money was classified over time.

A service calculation must not be the only place where receipt facts exist.

## Boundary with `core_new`

`core_new` separates data access from business logic.

`finance_core` separates money movement from service accounting.

These are complementary architectural boundaries.

## Core entities

The first implementation should distinguish at least:

1. `MoneyTransaction` — immutable receipt or expense fact.
2. `Allocation` — assignment of all or part of a transaction.
3. `AllocationTarget` — service, period, resident, vehicle, account, or another
   supported analytical target.
4. `Adjustment` — auditable correction without deleting history.

## Invariants

1. A transaction amount is never silently changed after posting.
2. The sum of active allocations cannot exceed the available transaction
   amount unless an explicit credit/overdraft rule permits it.
3. Unallocated amount is calculated, not manually duplicated.
4. Reallocation preserves the full audit trail.
5. Deletion is exceptional; normal correction uses reversing or adjusting
   records.
6. Every financial mutation records author, timestamp, reason, and source.

## Initial implementation sequence

1. Describe existing receipt tables and posting paths.
2. Introduce an explicit unallocated balance.
3. Extract allocations into a separate structure.
4. Add reallocation and correction commands.
5. Connect service balance calculations.
6. Add reconciliation and audit reports.

## Deferred decisions

The exact physical database schema, account chart mapping, and integration with
external accounting software remain implementation decisions and require
separate ADRs.
