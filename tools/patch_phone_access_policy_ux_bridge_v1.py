#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

TARGET = Path(r"G:\Programming\Py\OSBB\Bots\handlers\service_orders_workspace.py")
BACKUP_ROOT = Path(r"G:\Programming\Py\OSBB\Data\backups\source_patches")
MARKER = "OSBB_PHONE_ACCESS_POLICY_UX_BRIDGE_V1"

PHONE_INPUT_ANCHOR = """    if mode == "operator_phone_input":
        phone = re.sub(r"[\s()\-]", "", text(message_text))
        if not re.fullmatch(r"\+?\d{8,20}", phone):
            await update.message.reply_text(tr(lang, "wrong_phone")); return True
        order = service_order_summary(int(state["order_id"]))
        try:
            _activate_phone(order, phone, user_id)
        except Exception as exc:
            await update.message.reply_text(f"⚠️ {exc}"); return True
        await _show_operator_order(update, state, user_id, int(order["id"]), lang); return True
"""

PHONE_INPUT_BLOCK = """    if mode == "operator_phone_input":
        phone = re.sub(r"[\s()\-]", "", text(message_text))
        if not re.fullmatch(r"\+?\d{8,20}", phone):
            await update.message.reply_text(tr(lang, "wrong_phone")); return True
        order = service_order_summary(int(state["order_id"]))
        try:
            _activate_phone(order, phone, user_id)
        except Exception as exc:
            # OSBB_PHONE_ACCESS_POLICY_UX_BRIDGE_V1
            if ServiceAccessDenied is not None and isinstance(exc, ServiceAccessDenied):
                await update.message.reply_text(
                    result_to_short_text(exc.result) if result_to_short_text else f"⚠️ {exc}"
                )
                return True
            await update.message.reply_text(f"⚠️ {exc}"); return True
        await _show_operator_order(update, state, user_id, int(order["id"]), lang); return True
"""


def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = BACKUP_ROOT / f"phone_access_policy_ux_bridge_v1_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / path.name
    shutil.copy2(path, dst)
    return dst


def patch(data: str) -> tuple[str, list[str]]:
    changes = []
    if MARKER in data:
        changes.append("phone access UX bridge marker already present")
        return data, changes
    if "from service_access_policy import ServiceAccessDenied, result_to_short_text" not in data:
        raise RuntimeError("service_access_policy UX import is not present. Run workspace UX patch first.")
    if PHONE_INPUT_ANCHOR not in data:
        raise RuntimeError("operator_phone_input exception anchor not found.")
    data = data.replace(PHONE_INPUT_ANCHOR, PHONE_INPUT_BLOCK, 1)
    changes.append("patched operator_phone_input ServiceAccessDenied handling")
    return data, changes


def snippet(lines: list[str], needle: str, radius: int = 12) -> list[str]:
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
    print("OSBB phone access policy UX bridge v1")
    print("=" * 88)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Target:", target)
    print("Changed:", changed)
    print("")
    print("Planned changes:")
    for c in changes:
        print(" -", c)
    print("")
    print("---- phone activation exception preview ----")
    for line in snippet(new.splitlines(), MARKER):
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
