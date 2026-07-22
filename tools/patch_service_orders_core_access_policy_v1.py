#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
patch_service_orders_core_access_policy_v1.py

Installs service_access_policy integration into service_orders_core.create_service_order().

Safety:
  - DRY RUN by default.
  - Writes only with --apply.
  - Creates source backup before writing.
  - Patches only G:\Programming\Py\OSBB\service_orders_core.py by default.
  - Does not touch DB.

PowerShell:
  python .\OSBB\tools\patch_service_orders_core_access_policy_v1.py
  python .\OSBB\tools\patch_service_orders_core_access_policy_v1.py --apply
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


MARKER = "OSBB_SERVICE_ACCESS_POLICY_V1"
DEFAULT_TARGET = Path(r"G:\Programming\Py\OSBB\service_orders_core.py")
BACKUP_ROOT = Path(r"G:\Programming\Py\OSBB\Data\backups\source_patches")


ANCHOR_IMPORT = '''try:
    from access_control import has_permission
except Exception:
    has_permission = None
'''

IMPORT_BLOCK = '''try:
    from access_control import has_permission
except Exception:
    has_permission = None

try:
    from service_access_policy import ensure_service_order_allowed
except Exception:
    ensure_service_order_allowed = None
'''


ANCHOR_POLICY = '''        cur = conn.cursor()
        item = _service_item(cur, service_item_code)
        workflow = get_service_workflow(cur, service_item_code)

        request_allowed = int(workflow.get("resident_request_enabled") or 0) == 1
'''

POLICY_BLOCK = '''        cur = conn.cursor()
        item = _service_item(cur, service_item_code)
        workflow = get_service_workflow(cur, service_item_code)

        # OSBB_SERVICE_ACCESS_POLICY_V1
        # Critical security/business rule.
        # Service orders must NEVER bypass this check.
        # Read-only access/debt policy check before creating a service order.
        # BLOCK/ERROR raises ServiceAccessDenied inside ensure_service_order_allowed().
        # WARN/ALLOW returns a policy result and order creation continues.
        if ensure_service_order_allowed is None:
            raise RuntimeError(
                "Critical component 'service_access_policy' is unavailable. "
                "Service order creation cannot continue."
            )

        policy_result = ensure_service_order_allowed(
            conn=conn,
            apartment_number=apartment_number,
            service_item_code=service_item_code,
        )

        request_allowed = int(workflow.get("resident_request_enabled") or 0) == 1
'''


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def make_backup(target: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = BACKUP_ROOT / f"service_orders_core_access_policy_v1_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / target.name
    shutil.copy2(target, dst)
    return dst


def normalize_previous_soft_block(source: str) -> str:
    old = '''        # OSBB_SERVICE_ACCESS_POLICY_V1
        # Read-only access/debt policy check before creating a service order.
        # BLOCK/ERROR raises ServiceAccessDenied inside ensure_service_order_allowed().
        # WARN/ALLOW returns a policy result and order creation continues.
        if ensure_service_order_allowed is not None:
            policy_result = ensure_service_order_allowed(
                conn=conn,
                apartment_number=apartment_number,
                service_item_code=service_item_code,
            )
        else:
            policy_result = {
                "decision": "SKIPPED",
                "message": "service_access_policy module is not available.",
            }
'''
    replacement = '''        # OSBB_SERVICE_ACCESS_POLICY_V1
        # Critical security/business rule.
        # Service orders must NEVER bypass this check.
        # Read-only access/debt policy check before creating a service order.
        # BLOCK/ERROR raises ServiceAccessDenied inside ensure_service_order_allowed().
        # WARN/ALLOW returns a policy result and order creation continues.
        if ensure_service_order_allowed is None:
            raise RuntimeError(
                "Critical component 'service_access_policy' is unavailable. "
                "Service order creation cannot continue."
            )

        policy_result = ensure_service_order_allowed(
            conn=conn,
            apartment_number=apartment_number,
            service_item_code=service_item_code,
        )
'''
    return source.replace(old, replacement, 1)


def patch_source(source: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    patched = source

    if "service_access_policy module is not available" in patched:
        patched2 = normalize_previous_soft_block(patched)
        if patched2 != patched:
            patched = patched2
            changes.append("upgraded previous soft SKIPPED policy block to fail-fast RuntimeError")

    if MARKER in patched:
        changes.append("marker already present: no new policy block insertion needed")
    else:
        if ANCHOR_POLICY not in patched:
            raise RuntimeError("Policy insertion anchor not found in create_service_order().")
        patched = patched.replace(ANCHOR_POLICY, POLICY_BLOCK, 1)
        changes.append("inserted fail-fast policy check into create_service_order()")

    if "from service_access_policy import ensure_service_order_allowed" in patched:
        changes.append("import already present")
    else:
        if ANCHOR_IMPORT not in patched:
            raise RuntimeError("Import anchor not found.")
        patched = patched.replace(ANCHOR_IMPORT, IMPORT_BLOCK, 1)
        changes.append("added guarded import for ensure_service_order_allowed")

    return patched, changes


def snippet(lines: list[str], needle: str, radius: int = 10) -> list[str]:
    for i, line in enumerate(lines):
        if needle in line:
            start = max(0, i - radius)
            end = min(len(lines), i + radius + 1)
            return [f"{j+1}: {lines[j]}" for j in range(start, end)]
    return [f"(needle not found: {needle})"]


def show_preview(new: str) -> str:
    lines = new.splitlines()
    out: list[str] = []
    out.append("---- import preview ----")
    out.extend(snippet(lines, "service_access_policy", 8))
    out.append("")
    out.append("---- policy insertion preview ----")
    out.extend(snippet(lines, MARKER, 14))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=str(DEFAULT_TARGET), help="Path to service_orders_core.py")
    ap.add_argument("--apply", action="store_true", help="Write patched file.")
    args = ap.parse_args()

    target = Path(args.target)
    if not target.exists():
        raise SystemExit(f"Target not found: {target}")

    old = read(target)
    new, changes = patch_source(old)
    changed = old != new

    print("=" * 88)
    print("OSBB patch service_orders_core access policy v1 - fail-fast")
    print("=" * 88)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Target:", target)
    print("Changed:", changed)
    print("")
    print("Planned changes:")
    for c in changes:
        print(" -", c)
    print("")
    print(show_preview(new))
    print("")

    if not args.apply:
        print("DRY RUN COMPLETED. Re-run with --apply to install.")
        return 0

    if not changed:
        print("Nothing to write. APPLY COMPLETED.")
        return 0

    backup = make_backup(target)
    write(target, new)

    print("Installed.")
    print("Backup:", backup)
    print("Next:")
    print("  1) run import/preflight test for service_orders_core")
    print("  2) create a test service order for debtor apt 89 and TEST_REMOTE_NEW")
    print("APPLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
