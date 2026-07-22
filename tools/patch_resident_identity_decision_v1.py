#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_resident_identity_decision_v1.py

Fixes the architectural decision in project documentation.

Creates/updates:
- OSBB/docs/ROADMAP.md
- OSBB/docs/architecture/ADR-2026-07-01-resident-identity-refactor.md

DRY RUN by default.
Use --apply to write files.

Run from:
  G:\Programming\Py

Example:
  python .\OSBB\tools\patch_resident_identity_decision_v1.py

Apply:
  python .\OSBB\tools\patch_resident_identity_decision_v1.py --apply
"""

from __future__ import annotations

import argparse
from pathlib import Path


MARKER = "RESIDENT_IDENTITY_REFACTOR_V1"


ROADMAP_BLOCK = """
<!-- RESIDENT_IDENTITY_REFACTOR_V1:BEGIN -->

## P1. Resident Identity Refactor

Status: planned
Date decided: 2026-07-01
Reason: current resident/client/admin flow mixes several different concepts and creates confusing behavior.

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

Real resident acts for themselves.

Resident can:

- view and confirm their apartment;
- maintain personal contact data;
- add, edit, or request changes to vehicles;
- update parking-related attributes when allowed;
- submit remote/pult requests;
- submit phone barrier access requests;
- see charges, payments, and service history;
- confirm current profile data.

Important rule:

Resident may edit ordinary profile data directly, but changes affecting access, payments, permissions, or legal responsibility may require operator review.

Examples:

- contact phone: direct edit or light confirmation;
- vehicle plate: review if it affects access/payment;
- parking mode: review;
- gate phone number: service request/review;
- binding to another apartment: strict operator verification.

#### 2. Operator Workspace

Operator acts on request of a resident, but never becomes that resident.

Operator opens a resident/apartment card and performs actions from the operator role:

- create pult request;
- create phone access request;
- register payment;
- correct vehicle data;
- open service history;
- manage commercial/non-residential units;
- create or update contracts;
- record the source/reason of the action.

All such actions must be auditable as operator actions.

#### 3. Super-admin Diagnostic Resident View

Super-admin can temporarily view the bot as a chosen apartment/resident for diagnostics.

This is only a diagnostic view.

Rules:

- does not change Telegram ID binding;
- does not change resident_account;
- does not create apartment binding requests;
- does not change verification status;
- must show a visible "diagnostic view" banner;
- must have explicit exit;
- /start must reset this mode;
- optional timeout should auto-expire the mode.

### Legacy rule

The existing flow around "Змінити квартиру" and "Режим мешканця" is considered legacy and should not be expanded.

Before replacing it, create a diagnostic map of:

- where apartment change is handled;
- where binding requests are created;
- where verification status is changed;
- where Telegram ID is connected to resident/apartment;
- where admin client/resident mode is entered and exited.

### Next implementation step

Create a diagnostic tool:

`OSBB/tools/diagnose_resident_identity_flow.py`

Its purpose is to find and report code entry points of the old resident identity flow before replacement.

<!-- RESIDENT_IDENTITY_REFACTOR_V1:END -->
""".strip()


ADR_TEXT = """# ADR: Resident Identity Refactor

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
"""


def append_or_replace_marker(text: str, block: str) -> str:
    begin = "<!-- RESIDENT_IDENTITY_REFACTOR_V1:BEGIN -->"
    end = "<!-- RESIDENT_IDENTITY_REFACTOR_V1:END -->"

    if begin in text and end in text:
        start = text.index(begin)
        finish = text.index(end) + len(end)
        return text[:start].rstrip() + "\n\n" + block + "\n" + text[finish:].lstrip()

    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n" + block + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"G:\Programming\Py\OSBB")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    docs = root / "docs"
    arch = docs / "architecture"
    roadmap = docs / "ROADMAP.md"
    adr = arch / "ADR-2026-07-01-resident-identity-refactor.md"

    print("=" * 100)
    print("OSBB Resident Identity Refactor documentation patch")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Root:", root)
    print("Roadmap:", roadmap)
    print("ADR:", adr)

    old_roadmap = roadmap.read_text(encoding="utf-8") if roadmap.exists() else "# OSBB Roadmap\n"
    new_roadmap = append_or_replace_marker(old_roadmap, ROADMAP_BLOCK)

    changed = []
    if new_roadmap != old_roadmap:
        changed.append(str(roadmap))
    if (not adr.exists()) or adr.read_text(encoding="utf-8") != ADR_TEXT:
        changed.append(str(adr))

    print("")
    print("Planned changes:")
    if changed:
        for p in changed:
            print(" - write/update", p)
    else:
        print(" - no changes")

    if not args.apply:
        print("")
        print("DRY RUN COMPLETED. Re-run with --apply to write.")
        return 0

    docs.mkdir(parents=True, exist_ok=True)
    arch.mkdir(parents=True, exist_ok=True)
    roadmap.write_text(new_roadmap, encoding="utf-8")
    adr.write_text(ADR_TEXT, encoding="utf-8")

    print("")
    print("APPLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
