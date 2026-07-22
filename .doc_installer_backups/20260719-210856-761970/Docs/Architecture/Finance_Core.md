# Finance Core — Architectural Direction

## Why this document exists

The Finance Core direction arose from a concrete cashier-journal case rather than from an abstract redesign.

A payment of **700 UAH** was received:

- 500 UAH was clearly intended for night parking;
- 200 UAH had no confirmed purpose.

The existing model does not handle this correctly because it expects all received money to be immediately attached to a known service.

## Architectural shift

The project must separate:

```text
receipt of money
```

from:

```text
allocation of money
```

These are related but different events.

## Target model

```text
Financial receipt
    amount: 700 UAH

Allocation A
    amount: 500 UAH
    purpose: night parking

Unallocated remainder
    amount: 200 UAH
```

The unallocated remainder is not an error and must not be hidden. It is a valid temporary financial state that requires later attention.

## Future operations

An unallocated or previously allocated amount may later be:

- allocated;
- reallocated;
- carried forward;
- recognized as an advance;
- refunded;
- corrected;
- transferred to another obligation.

Each operation must preserve the previous state in the audit history.

## Position in OSBB architecture

Finance Core should become a dedicated domain layer, comparable in importance to `core_new`.

```text
Telegram / Cashier UI / Reports
               |
               v
         finance_core
               |
               v
            core_new
               |
               v
            Database
```

User interfaces collect intent and display results. They must not independently decide financial rules.

## First milestone

The first milestone is deliberately narrow:

> Correctly record 700 UAH, allocate 500 UAH to night parking, and retain 200 UAH as visible unallocated money that can be assigned later.

This scenario is the acceptance test for the first Finance Core implementation.

## Naming

The current preferred working name is:

```text
finance_core
```

Possible alternatives such as `core_finance` should be resolved before creating the package. Until that decision, documentation uses `finance_core`.
