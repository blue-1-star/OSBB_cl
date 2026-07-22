#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
admin_manager.py

Permanent bot_admins manager.

Examples:
  python .\OSBB\tools\admin_manager.py --list
  python .\OSBB\tools\admin_manager.py --show 1198872172
  python .\OSBB\tools\admin_manager.py --add --id 1198872172 --name "Виктория" --role admin --apply
  python .\OSBB\tools\admin_manager.py --disable --id 1198872172 --apply
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

DEFAULT_DB = r"G:\Programming\Py\OSBB\Data\db\osbb_test.db"

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def backup_db(db: Path, reason: str) -> Path:
    bdir = db.parent / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dst = bdir / f"{db.stem}_before_admin_manager_{reason}_{datetime.now():%Y-%m-%d_%H-%M-%S}{db.suffix}"
    shutil.copy2(db, dst)
    return dst

def print_rows(rows) -> None:
    if not rows:
        print("No rows.")
        return
    print("id | telegram_user_id | username | display_name | role | R W U P B | active | updated_at | notes")
    print("-" * 120)
    for r in rows:
        print(
            f"{r['id']} | {r['telegram_user_id']} | {r['telegram_username'] or ''} | "
            f"{r['display_name'] or ''} | {r['role'] or ''} | "
            f"{r['can_read']} {r['can_write']} {r['can_manage_users']} {r['can_manage_payments']} {r['can_manage_bot']} | "
            f"{r['is_active']} | {r['updated_at'] or ''} | {r['notes'] or ''}"
        )

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)

    action = ap.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true")
    action.add_argument("--show", type=int)
    action.add_argument("--add", action="store_true")
    action.add_argument("--update", action="store_true")
    action.add_argument("--disable", action="store_true")
    action.add_argument("--enable", action="store_true")
    action.add_argument("--remove", action="store_true")

    ap.add_argument("--id", type=int)
    ap.add_argument("--username", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--role", default="admin")
    ap.add_argument("--can-read", type=int, default=1)
    ap.add_argument("--can-write", type=int, default=1)
    ap.add_argument("--can-manage-users", type=int, default=1)
    ap.add_argument("--can-manage-payments", type=int, default=1)
    ap.add_argument("--can-manage-bot", type=int, default=0)
    ap.add_argument("--notes", default="")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        if args.list:
            rows = conn.execute(
                "SELECT * FROM bot_admins ORDER BY is_active DESC, role, display_name, telegram_user_id"
            ).fetchall()
            print_rows(rows)
            return 0

        target_id = args.show or args.id
        if not target_id:
            raise SystemExit("--id is required for this action")

        if args.show:
            rows = conn.execute("SELECT * FROM bot_admins WHERE telegram_user_id=?", (target_id,)).fetchall()
            print_rows(rows)
            return 0

        print("=" * 100)
        print("OSBB admin manager")
        print("=" * 100)
        print("Mode:", "APPLY" if args.apply else "DRY RUN")
        print("DB:", db)
        print("Telegram ID:", target_id)

        if not args.apply:
            print("DRY RUN COMPLETED. Re-run with --apply to write.")
            return 0

        backup = backup_db(db, "change")
        print("Backup:", backup)

        if args.add or args.update:
            existing = conn.execute("SELECT id FROM bot_admins WHERE telegram_user_id=?", (target_id,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE bot_admins
                    SET telegram_username=?, display_name=?, role=?,
                        can_read=?, can_write=?, can_manage_users=?,
                        can_manage_payments=?, can_manage_bot=?,
                        is_active=1, updated_at=?, notes=?
                    WHERE telegram_user_id=?
                    """,
                    (
                        args.username or None, args.name or None, args.role,
                        args.can_read, args.can_write, args.can_manage_users,
                        args.can_manage_payments, args.can_manage_bot,
                        now(), args.notes or None, target_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO bot_admins (
                        telegram_user_id, telegram_username, display_name, role,
                        can_read, can_write, can_manage_users, can_manage_payments,
                        can_manage_bot, is_active, created_at, updated_at, notes
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        target_id, args.username or None, args.name or None, args.role,
                        args.can_read, args.can_write, args.can_manage_users,
                        args.can_manage_payments, args.can_manage_bot,
                        now(), now(), args.notes or None,
                    ),
                )
        elif args.disable:
            conn.execute("UPDATE bot_admins SET is_active=0, updated_at=? WHERE telegram_user_id=?", (now(), target_id))
        elif args.enable:
            conn.execute("UPDATE bot_admins SET is_active=1, updated_at=? WHERE telegram_user_id=?", (now(), target_id))
        elif args.remove:
            conn.execute("DELETE FROM bot_admins WHERE telegram_user_id=?", (target_id,))

        conn.commit()
        print("")
        print("APPLY COMPLETED")
        rows = conn.execute("SELECT * FROM bot_admins WHERE telegram_user_id=?", (target_id,)).fetchall()
        print_rows(rows)
        return 0
    finally:
        conn.close()

if __name__ == "__main__":
    raise SystemExit(main())
