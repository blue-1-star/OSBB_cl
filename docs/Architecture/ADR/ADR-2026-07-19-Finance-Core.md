# ADR-2026-07-19: Separate Money Movement from Service Accounting

- **Status:** Accepted
- **Date:** 2026-07-19
- **Decision owner:** OSBB architecture

## Context

The cashier can receive money before the complete purpose of that money is
known. The current model tends to require every received amount to be assigned
immediately to a service.

A real case exposed the problem: 700 UAH was received, 500 UAH belonged to
overnight parking, while the purpose of the remaining 200 UAH was not known at
the moment of receipt.

Forcing an immediate service assignment either loses information, invents a
classification, or prevents registration of the actual receipt.

## Decision

Introduce a dedicated financial domain with the working name `finance_core`.

The system records money movement first and records allocations separately.
A transaction may therefore be fully allocated, partially allocated, or
temporarily unallocated.

## Consequences

### Positive

- actual receipts can always be registered;
- uncertain purpose is represented honestly;
- reallocations no longer require rewriting receipt history;
- advances, refunds, and corrections gain a common model;
- auditability improves;
- service modules become less coupled to cashier input.

### Costs

- additional entities and invariants are required;
- balances must distinguish received, allocated, and unallocated amounts;
- migration of existing records will be required;
- user interfaces must expose allocation state clearly.

## Rejected alternative

Require the cashier to select a service for the full amount immediately.

This was rejected because it makes the interface invent accounting facts that
may not yet be known and cannot represent partial allocation truthfully.

## Follow-up

- document existing financial tables;
- design the first allocation schema;
- define correction and reversal policy;
- plan migration and reconciliation;
- create implementation ADRs for database and API details.
