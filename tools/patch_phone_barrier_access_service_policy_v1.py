#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
patch_phone_barrier_access_service_policy_v1.py

Patch phone_barrier_access_service.py:
  - add guarded import of ensure_service_order_allowed
  - fail-fast if policy module is unavailable
  - call policy before create_service_interest() inside create_phone_barrier_access_interest()

Safety:
  - DRY RUN by default
  - --apply writes file
  - creates source backup before APPLY
  - idempotent marker check

PowerShell:

  python .\OSBB\tools\patch_phone_barrier_access_service_policy_v1.py

  python .\OSBB\tools\patch_phone_barrier_access_service_policy_v1.py --apply
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


TARGET = Path(r"G:\Programming\Py\OSBB\phone_barrier_access_service.py")
BACKUP_ROOT = Path(r"G:\Programming\Py\OSBB\Data\backups\source_patches")
MARKER = "OSBB_PHONE_ACCESS_POLICY_V1"


IMPORT_ANCHOR = """from service_orders_core import get_conn, text
from service_preorders_core import create_service_interest, ensure_simplified_service_schema
"""

IMPORT_BLOCK = """from service_orders_core import get_conn, text
from service_preorders_core import create_service_interest, ensure_simplified_service_schema

try:
    from service_access_policy import ensure_service_order_allowed
except Exception:
    ensure_service_order_allowed = None
"""


INSERT_ANCHOR = """        quote = quote_phone_barrier_access(
            access_point_codes=points,
            registration_date=quote_date,
            conn=conn,
        )
        quantity = len(points)
"""

INSERT_BLOCK = """        quote = quote_phone_barrier_access(
            access_point_codes=points,
            registration_date=quote_date,
            conn=conn,
        )

        # OSBB_PHONE_ACCESS_POLICY_V1
        # Business Policy must be checked before a phone-access interest is created.
        # BLOCK/ERROR raises ServiceAccessDenied inside ensure_service_order_allowed().
        # WARN/ALLOW returns and interest creation continues.
        if ensure_service_order_allowed is None:
            raise RuntimeError(
                "Critical component 'service_access_policy' is unavailable. "
                "Phone access interest creation cannot continue."
            )

        policy_result = ensure_service_order_allowed(
            conn=conn,
            apartment_number=text(apartment_number),
            service_item_code=service_item_code,
        )

        quantity = len(points)
"""


def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = BACKUP_ROOT / f"phone_barrier_access_service_policy_v1_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / path.name
    shutil.copy2(path, dst)
    return dst


def patch(data: str) -> tuple[str, list[str]]:
    changes: list[str] = []

    if MARKER in data:
        changes.append("policy marker already present")
        return data, changes

    if "from service_access_policy import ensure_service_order_allowed" not in data:
        if IMPORT_ANCHOR not in data:
            raise RuntimeError("Import anchor not found.")
        data = data.replace(IMPORT_ANCHOR, IMPORT_BLOCK, 1)
        changes.append("added guarded import for ensure_service_order_allowed")

    if INSERT_ANCHOR not in data:
        raise RuntimeError("Policy insertion anchor not found.")

    data = data.replace(INSERT_ANCHOR, INSERT_BLOCK, 1)
    changes.append("inserted phone access policy check before create_service_interest()")
    return data, changes


def snippet(lines: list[str], needle: str, radius: int = 14) -> list[str]:
    for i, line in enumerate(lines):
        if needle in line:
            a = max(0, i - radius)
            b = min(len(lines), i + radius + 1)
            return [f"{j+1}: {lines[j]}" for j in range(a, b)]
    return [f"(needle not found: {needle})"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=str(TARGET))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    target = Path(args.target)
    if not target.exists():
        raise SystemExit(f"Target not found: {target}")

    old = target.read_text(encoding="utf-8")
    new, changes = patch(old)
    changed = old != new

    print("=" * 88)
    print("OSBB patch phone_barrier_access_service policy v1")
    print("=" * 88)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Target:", target)
    print("Changed:", changed)
    print("")
    print("Planned changes:")
    for c in changes:
        print(" -", c)

    print("")
    print("---- import preview ----")
    for line in snippet(new.splitlines(), "ensure_service_order_allowed", 10):
        print(line)

    print("")
    print("---- policy insertion preview ----")
    for line in snippet(new.splitlines(), MARKER, 16):
        print(line)

    print("")

    if not args.apply:
        print("DRY RUN COMPLETED. Re-run with --apply to install.")
        return 0

    if not changed:
        print("Nothing to write. APPLY COMPLETED.")
        return 0

    b = backup(target)
    target.write_text(new, encoding="utf-8")
    print("Installed.")
    print("Backup:", b)
    print("APPLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
