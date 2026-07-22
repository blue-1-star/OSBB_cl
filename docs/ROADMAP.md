# OSBB Roadmap

<!-- RESIDENT_IDENTITY_REFACTOR_V1:BEGIN -->

## P1. Resident Identity Refactor

**Status:** planned  
**Date decided:** 2026-07-01  
**Reason:** current resident/client/admin flow mixes several different concepts and creates confusing behavior.

### Problem

The current legacy flow around "Режим мешканця", "Змінити квартиру", apartment binding, resident verification, and admin testing is mixed.

It currently blends together:
- resident self-service cabinet;
- apartment binding and verification;
- operator work on behalf of a resident;
- super-admin diagnostic view;
- real Telegram ID to apartment binding.

This creates unclear states, especially for super-admins and operators.

### Decision

Split the old mixed flow into three separate flows.

#### 1. Resident Cabinet
Real resident acts for themselves. Resident can:
- view and confirm their apartment;
- maintain personal contact data;
- add, edit, or request changes to vehicles;
- submit remote/pult requests;
- see charges, payments, and service history.

#### 2. Operator Workspace
Operator acts on request of a resident, but never becomes that resident. All actions must be auditable.

#### 3. Super-admin Diagnostic Resident View
Super-admin can temporarily view the bot as a chosen apartment/resident for diagnostics.

### Legacy rule
The existing flow around "Змінити квартиру" and "Режим мешканця" is considered legacy and should not be expanded.

<!-- RESIDENT_IDENTITY_REFACTOR_V1:END -->

<!-- CORE_NEW_LAYER_V1:BEGIN -->

## P2. New Architecture Layer (core_new)

**Status:** completed (2026-07-13)

### What was done

Created a new architectural layer `core_new/` that separates business logic from interface code.

#### 1. Adapters
Created `DBAdapter` — a wrapper around the legacy `Bots/db_access.py`.

#### 2. Domain Models

| Model | Status | Tests |
|-------|--------|-------|
| `Vehicle` | ✅ | ✅ |
| `Resident` | ✅ | ✅ |
| `Apartment` | ✅ | ✅ |
| `Payment` | ✅ | ✅ |
| `VehicleCandidate` | ✅ | ✅ |

#### 3. Testing

All models have working tests in `tests/`.

#### 4. Documentation

- [ADR-2026-07-13-core-new-layer.md](Architecture/ADR-2026-07-13-core-new-layer.md)
- [Domain models](../Domain/README.md)
- [core_new code docs](../Code/core_new.md)

### Next steps

1. Replace legacy calls with new domain models
2. Integrate all models into Telegram bot
3. Remove duplicate code

<!-- CORE_NEW_LAYER_V1:END -->

<!-- CASHIER_VEHICLE_CANDIDATE_V1:BEGIN -->

## P3. Cashier and Vehicle Candidate (В работе)

**Status:** in progress (2026-07-14)

### Problem

Cashier cannot accept payments for vehicles without an apartment.

### Decision

Create a new entity: `vehicle_candidates`.

### Lifecycle

PENDING -> RESOLVED -> vehicle created in vehicles
PENDING -> REJECTED

### Next steps

1. Integrate `vehicle_candidates` into cashier UI
2. Add operator workspace for reviewing candidates
3. Link payments to candidates via `candidate_id`

<!-- CASHIER_VEHICLE_CANDIDATE_V1:END -->

<!-- OSBB-DOCS:BEGIN finance-core -->
## Finance Core

**Architectural direction:** separate money movement from service accounting.

Planned stages:

1. Inventory current receipt, expense, allocation, and balance paths.
2. Introduce explicit partial and unallocated receipt amounts.
3. Store allocations independently from the original money transaction.
4. Support auditable reallocation, advance, refund, and correction operations.
5. Connect service balances to allocation history.
6. Add reconciliation and audit reports.

Implementation details will be recorded in separate ADRs.
<!-- OSBB-DOCS:END finance-core -->

<!-- BEGIN: DEVELOPMENT-DOCS-V1 -->
## Development Docs v1

- [x] Создать `Docs/Development`.
- [x] Зафиксировать инженерные принципы.
- [x] Создать Lessons Learned и Troubleshooting.
- [x] Зафиксировать окружение.
- [x] Описать Presentation Layer и Query Library.
- [ ] Реализовать `assistant doctor`.
- [ ] Реализовать `assistant env`.
- [ ] Реализовать `assistant where`.
- [ ] Реализовать `assistant verify`.
- [ ] Научить Assistant сопровождать инженерную документацию.
<!-- END: DEVELOPMENT-DOCS-V1 -->
