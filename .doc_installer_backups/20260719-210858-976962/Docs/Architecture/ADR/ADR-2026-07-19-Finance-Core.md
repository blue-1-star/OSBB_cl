# ADR: Formation of a Finance Core

- **Status:** Proposed
- **Date:** 2026-07-19
- **Area:** Architecture / Finance
- **Working name:** `finance_core`
- **Related subsystem:** `core_new`

## Context

During discussion of the cashier journal, a real payment case exposed a limitation in the current financial model.

A cashier received **700 UAH**:

- **500 UAH** had a known purpose: night parking;
- **200 UAH** had no reliably established purpose at the moment of receipt.

The current model expects a payment to be immediately and fully assigned to a service or obligation. It cannot naturally represent money that has been received but is only partially classified.

This is not merely a cashier-interface problem. It is a limitation of the financial domain model.

## Problem

The system currently tends to combine two different facts into one operation:

1. money was received;
2. the money was assigned to a specific service, period, debt, advance, or other purpose.

In real work these facts may occur at different times.

The system must not invent a purpose for the unclassified part of a payment, reject the real payment, or distort the cashier journal merely because classification is incomplete.

## Decision

Develop a separate financial core under the working name:

```text
finance_core
```

The final package name may be refined before implementation, but the architectural direction is accepted.

The Finance Core must treat receipt of money as an independent financial event. Classification and allocation of that money are separate operations.

Example:

```text
Receipt:
    700 UAH received

Allocations:
    500 UAH -> night parking
    200 UAH -> unallocated
```

Later, the remaining 200 UAH may be:

- assigned to another service;
- applied to a future period;
- treated as an advance;
- transferred between obligations;
- refunded;
- corrected through an auditable operation.

## Core principle

> Money is first registered as received. Its purpose may be established or changed afterward.

A receipt must remain historically stable. Subsequent allocation, reallocation, correction, or refund must be represented by new financial operations rather than destructive editing of history.

## Responsibilities of Finance Core

The Finance Core is expected to become the single domain layer for:

- receipts;
- expenses and payouts;
- payment allocations;
- unallocated balances;
- advances;
- reallocations;
- refunds;
- corrections;
- transfers between services or periods;
- resident and service balances;
- tariff-aware calculations;
- audit history of financial changes.

## Relationship with `core_new`

`core_new` provides a controlled access layer for project data.

`finance_core` will provide controlled financial behavior and invariants.

```text
core_new      -> how the system accesses and stores data
finance_core  -> how money moves and how financial meaning is recorded
```

Finance Core may use `core_new`, but financial rules must not be scattered across the Telegram UI, cashier screens, SQL queries, or unrelated modules.

## Required invariants

1. The total allocated amount must never exceed the available amount of the source receipt unless a separately documented credit mechanism exists.
2. The sum of active allocations plus the unallocated remainder must equal the receipt amount.
3. Reallocation must preserve history.
4. Corrections must be auditable.
5. A receipt must not disappear when its purpose changes.
6. UI modules must call Finance Core rather than implement independent balance rules.
7. Monetary values must use exact decimal representation, never binary floating point.
8. Every financial operation must have a timestamp, source, author or actor, and stable identifier.

## Initial implementation boundary

The first useful vertical slice should support:

1. registering a receipt;
2. allocating part or all of it;
3. preserving an unallocated remainder;
4. displaying the receipt and its allocation state;
5. later allocating the remainder;
6. producing an audit trail.

The first implementation should solve the concrete cashier case before expanding into a complete accounting subsystem.

## Consequences

### Positive

- Real cashier operations can be recorded without inventing data.
- Partial and unknown-purpose payments become normal scenarios.
- Advances, reallocations, refunds, and corrections share one coherent model.
- Balance calculations can move out of UI code.
- Future integration with formal accounting becomes possible.

### Costs and risks

- Existing payment tables and balance calculations must be reviewed.
- Migration rules will be required.
- Temporary coexistence with the old model may be necessary.
- The package name and detailed schema still require implementation design.
- Scope must be controlled to avoid attempting a complete accounting system in the first iteration.

## Direction

This ADR establishes the architectural direction, not the final database schema or API.

The next development task is to inspect the existing payment model and prepare the minimal Finance Core design needed for the 700/500/200 cashier scenario.
