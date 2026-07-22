# ADR: Resident Identity Refactor

Date: 2026-07-01
Status: Accepted for roadmap
Marker: RESIDENT_IDENTITY_REFACTOR_V1

## Context

During testing of the promoted training database, the current flow around resident identity became unclear.

The same visible actions appeared to mix several concepts:

- resident self-service;
- changing apartment;
- apartment verification;
- admin entering client/resident mode;
- operator acting for a resident;
- super-admin testing access and debt scenarios.

This caused confusing behavior: super-admin could appear as an ordinary resident of an unexpected apartment, apartment changes created binding requests, and self-confirmation looked stricter in text than in actual behavior.

## Decision

Do not continue extending the old mixed logic.

Introduce three separate concepts.

## 1. Resident Cabinet

The resident acts for themselves.

Allowed resident operations include:

- view and confirm apartment;
- edit personal profile data;
- manage contact phones;
- add/edit/request changes for vehicles;
- submit service requests;
- order remotes/pults;
- request phone barrier access;
- view charges and payments;
- view service history.

Changes that affect access, charges, permissions, or legal responsibility may require operator approval.

## 2. Operator Workspace

The operator does not emulate the resident.

The operator opens a resident/apartment/unit card and performs actions as operator:

- create order on resident request;
- accept/register payment;
- correct vehicle data;
- manage phone access;
- manage pult requests;
- manage commercial/non-residential units;
- create/update contracts.

All actions must keep operator identity and be audit logged.

## 3. Super-admin Diagnostic Resident View

This is the only actual emulation-like mode.

Purpose: diagnostics and reproduction of resident-side behavior.

Rules:

- temporary context only;
- no Telegram ID rebinding;
- no apartment binding request;
- no verification status change;
- no resident_account reassignment;
- visible banner;
- explicit exit;
- `/start` resets the mode;
- optional timeout.

## Non-goals

This decision does not immediately rewrite the bot.

The first implementation step is to map the current legacy flow and then replace it in controlled phases.

## Implementation notes

Recommended future modules:

- `resident_context.py`
- `resident_binding.py`
- `operator_workspace.py`
- `admin_diagnostic_view.py`

Recommended first tool:

- `tools/diagnose_resident_identity_flow.py`

## Legacy status

The current mixed "Режим мешканця / Змінити квартиру" flow is legacy.

It should be audited, isolated, and replaced, not extended.
