#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
patch_parking_bot_admin_access_v1.py

Urgent patch for Bots/parking_bot.py:
  1) keeps hardcoded ADMIN_IDS / SUPER_ADMIN_IDS working;
  2) adds temporary admin IDs from OSBB_EXTRA_ADMIN_IDS;
  3) adds DB admins from bot_admins where is_active=1;
  4) exposes Admin mode to these admins where menu logic used SUPER_ADMIN_IDS.

Safety:
  - DRY RUN by default
  - --apply required to write
  - backup created before write
  - idempotent marker OSBB_ADMIN_ACCESS_V1
"""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path


MARKER = "OSBB_ADMIN_ACCESS_V1"
HELPER = '\n# OSBB_ADMIN_ACCESS_V1\ndef _osbb_extra_admin_ids() -> set[int]:\n    import os\n    result: set[int] = set()\n    raw = os.environ.get("OSBB_EXTRA_ADMIN_IDS", "")\n    for part in raw.replace(";", ",").split(","):\n        part = part.strip()\n        if not part:\n            continue\n        try:\n            result.add(int(part))\n        except ValueError:\n            pass\n    return result\n\n\ndef _osbb_db_file_for_admin_check():\n    try:\n        from config import paths, USE_TEST_DB\n        return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE\n    except Exception:\n        return None\n\n\ndef _osbb_is_bot_admin(user_id: int) -> bool:\n    try:\n        if int(user_id) in _osbb_extra_admin_ids():\n            return True\n    except Exception:\n        pass\n\n    db_file = _osbb_db_file_for_admin_check()\n    if not db_file:\n        return False\n\n    try:\n        import sqlite3\n        conn = sqlite3.connect(str(db_file))\n        try:\n            cur = conn.cursor()\n            exists = cur.execute(\n                "SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=\'bot_admins\'"\n            ).fetchone()\n            if not exists:\n                return False\n            row = cur.execute(\n                "SELECT is_active FROM bot_admins WHERE CAST(telegram_user_id AS TEXT) = ? LIMIT 1",\n                (str(user_id),),\n            ).fetchone()\n            return bool(row and int(row[0] or 0) == 1)\n        finally:\n            conn.close()\n    except Exception:\n        return False\n'
NEW_IS_ADMIN = 'def is_admin_user(user_id: int) -> bool:\n    """Return True for hardcoded admins, temporary env admins, or active bot_admins."""\n    return (\n        user_id in ADMIN_IDS\n        or user_id in SUPER_ADMIN_IDS\n        or _osbb_is_bot_admin(user_id)\n    )\n'


def backup(path: Path) -> Path:
    bdir = path.parent / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dst = bdir / f"{path.stem}_before_admin_access_v1_{datetime.now():%Y-%m-%d_%H-%M-%S}{path.suffix}"
    shutil.copy2(path, dst)
    return dst


def replace_is_admin(text: str) -> tuple[str, bool]:
    m = re.search(r"^def\s+is_admin_user\s*\(\s*user_id\s*:\s*int\s*\)\s*->\s*bool\s*:", text, re.M)
    if not m:
        raise RuntimeError("Could not find def is_admin_user(user_id: int) -> bool.")
    after = text[m.end():]
    stop = re.search(r"^(def |class |async def |@)", after, re.M)
    end = m.end() + (stop.start() if stop else 0)
    old = text[m.start():end]
    if "_osbb_is_bot_admin" in old:
        return text, False
    return text[:m.start()] + NEW_IS_ADMIN + "\n" + text[end:], True


def insert_helper(text: str) -> tuple[str, bool]:
    if MARKER in text:
        return text, False
    m = re.search(r"^def\s+is_admin_user\s*\(", text, re.M)
    if not m:
        raise RuntimeError("Could not find is_admin_user insertion point.")
    return text[:m.start()] + HELPER + "\n" + text[m.start():], True


def patch_admin_mode_checks(text: str) -> tuple[str, int]:
    lines = text.splitlines(True)
    changed = 0
    out = []
    for i, line in enumerate(lines):
        if "user_id in SUPER_ADMIN_IDS" not in line:
            out.append(line)
            continue
        lo = max(0, i - 12)
        hi = min(len(lines), i + 13)
        ctx = "".join(lines[lo:hi]).lower()
        menu_words = [
            "адмін", "админ", "admin", "режим", "mode",
            "режим мешканця", "режим жителя", "resident",
        ]
        if any(w in ctx for w in menu_words):
            out.append(line.replace("user_id in SUPER_ADMIN_IDS", "is_admin_user(user_id)"))
            changed += 1
        else:
            out.append(line)
    return "".join(out), changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", required=True, help="Path to OSBB/Bots/parking_bot.py")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    path = Path(args.bot)
    if not path.exists():
        raise SystemExit(f"Bot file not found: {path}")

    src = path.read_text(encoding="utf-8")
    dst = src

    planned = []
    dst, helper_changed = insert_helper(dst)
    if helper_changed:
        planned.append("insert OSBB_ADMIN_ACCESS_V1 helper functions")

    dst, is_admin_changed = replace_is_admin(dst)
    if is_admin_changed:
        planned.append("replace is_admin_user() to include bot_admins and OSBB_EXTRA_ADMIN_IDS")

    dst, mode_changes = patch_admin_mode_checks(dst)
    if mode_changes:
        planned.append(f"replace {mode_changes} admin-mode/menu SUPER_ADMIN_IDS check(s) with is_admin_user(user_id)")

    print("=" * 100)
    print("Patch parking_bot admin access v1")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Target:", path)
    print("Changed:", dst != src)
    print("")
    print("Planned changes:")
    if planned:
        for item in planned:
            print(" -", item)
    else:
        print(" - none")

    if not args.apply:
        print("")
        print("DRY RUN COMPLETED. Re-run with --apply to write.")
        return 0

    if dst == src:
        print("")
        print("APPLY COMPLETED: no changes needed.")
        return 0

    b = backup(path)
    path.write_text(dst, encoding="utf-8")
    print("")
    print("Backup:", b)
    print("APPLY COMPLETED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
