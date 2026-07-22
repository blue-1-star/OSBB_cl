#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

TARGET = Path(r"G:\Programming\Py\OSBB\Bots\handlers\service_orders_workspace.py")
BACKUP_ROOT = Path(r"G:\Programming\Py\OSBB\Data\backups\source_patches")
MARKER = "OSBB_SERVICE_POLICY_UX_V1"

UX_IMPORT = '''
try:
    from service_access_policy import ServiceAccessDenied, result_to_short_text
except Exception:
    ServiceAccessDenied = None
    result_to_short_text = None
'''.strip("\n")

RESIDENT_ANCHOR = '''            try:
                interest = _create_interest_from_state(state, user_id)
            except Exception as exc:
                await update.message.reply_text(f"⚠️ {exc}")
                return True
            await update.message.reply_text(tr(lang, "interest_saved"))
'''

RESIDENT_BLOCK = '''            try:
                interest = _create_interest_from_state(state, user_id)
            except Exception as exc:
                # OSBB_SERVICE_POLICY_UX_V1
                if ServiceAccessDenied is not None and isinstance(exc, ServiceAccessDenied):
                    await update.message.reply_text(
                        result_to_short_text(exc.result) if result_to_short_text else f"⚠️ {exc}"
                    )
                    return True
                await update.message.reply_text(f"⚠️ {exc}")
                return True
            await update.message.reply_text(tr(lang, "interest_saved"))
'''

OPERATOR_ANCHOR = '''        try:
            if action == "program_done":
                _program(order, user_id)
            elif action == "return_remote":
                _return_own_remote(order, user_id)
            elif action == "issue_new":
                issue_new_remotes_from_batch(service_order_id=int(order["id"]), actor_id=user_id)
        except Exception as exc:
            await update.message.reply_text(f"⚠️ {exc}"); return True
        await _show_operator_order(update, state, user_id, int(order["id"]), lang); return True
'''

OPERATOR_BLOCK = '''        try:
            if action == "program_done":
                _program(order, user_id)
            elif action == "return_remote":
                _return_own_remote(order, user_id)
            elif action == "issue_new":
                issue_new_remotes_from_batch(service_order_id=int(order["id"]), actor_id=user_id)
        except Exception as exc:
            # OSBB_SERVICE_POLICY_UX_V1
            if ServiceAccessDenied is not None and isinstance(exc, ServiceAccessDenied):
                await update.message.reply_text(
                    result_to_short_text(exc.result) if result_to_short_text else f"⚠️ {exc}"
                )
                return True
            await update.message.reply_text(f"⚠️ {exc}"); return True
        await _show_operator_order(update, state, user_id, int(order["id"]), lang); return True
'''


def add_import(data: str) -> tuple[str, str]:
    if "from service_access_policy import ServiceAccessDenied, result_to_short_text" in data:
        return data, "service_access_policy UX import already present"
    lines = data.splitlines()
    insert_after = None
    for i, line in enumerate(lines):
        if line.startswith("from service_preorders_core import "):
            if line.rstrip().endswith("("):
                depth = line.count("(") - line.count(")")
                j = i + 1
                while j < len(lines):
                    depth += lines[j].count("(") - lines[j].count(")")
                    if depth <= 0:
                        insert_after = j + 1
                        break
                    j += 1
            else:
                insert_after = i + 1
            break
    if insert_after is None:
        last_import = None
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                last_import = i + 1
            elif line.startswith("def ") or line.startswith("async def "):
                break
        insert_after = last_import
    if insert_after is None:
        raise RuntimeError("Could not find a safe import insertion point.")
    new_lines = lines[:insert_after] + UX_IMPORT.splitlines() + lines[insert_after:]
    return "\n".join(new_lines) + ("\n" if data.endswith("\n") else ""), "added guarded UX import flexibly"


def patch(data: str) -> tuple[str, list[str]]:
    changes = []
    data, change = add_import(data)
    changes.append(change)

    if MARKER in data:
        changes.append("UX marker already present")
        return data, changes

    if RESIDENT_ANCHOR not in data:
        raise RuntimeError("Resident create-interest exception anchor not found.")
    data = data.replace(RESIDENT_ANCHOR, RESIDENT_BLOCK, 1)
    changes.append("patched resident create-interest exception handling")

    if OPERATOR_ANCHOR not in data:
        raise RuntimeError("Operator action exception anchor not found.")
    data = data.replace(OPERATOR_ANCHOR, OPERATOR_BLOCK, 1)
    changes.append("patched operator action exception handling")
    return data, changes


def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = BACKUP_ROOT / f"service_orders_workspace_policy_ux_v1_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / path.name
    shutil.copy2(path, dst)
    return dst


def snippet(lines, needle, radius=10):
    for i, line in enumerate(lines):
        if needle in line:
            a = max(0, i - radius)
            b = min(len(lines), i + radius + 1)
            return [f"{j+1}: {lines[j]}" for j in range(a, b)]
    return [f"(needle not found: {needle})"]


def preview(data: str) -> str:
    lines = data.splitlines()
    out = ["---- import preview ----"]
    out.extend(snippet(lines, "service_access_policy", 8))
    idxs = [i for i, line in enumerate(lines) if MARKER in line]
    if idxs:
        out.append("")
        out.append("---- resident exception preview ----")
        i = idxs[0]
        a = max(0, i - 10)
        b = min(len(lines), i + 12)
        out.extend(f"{j+1}: {lines[j]}" for j in range(a, b))
    if len(idxs) > 1:
        out.append("")
        out.append("---- operator exception preview ----")
        i = idxs[1]
        a = max(0, i - 10)
        b = min(len(lines), i + 12)
        out.extend(f"{j+1}: {lines[j]}" for j in range(a, b))
    return "\n".join(out)


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
    print("OSBB service_orders_workspace policy UX patch v1")
    print("=" * 88)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Target:", target)
    print("Changed:", changed)
    print("")
    print("Planned changes:")
    for c in changes:
        print(" -", c)
    print("")
    print(preview(new))
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
