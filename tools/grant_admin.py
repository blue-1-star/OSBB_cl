#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
grant_admin.py

Adds or updates one administrator in bot_admins.

Default: Виктория / 1198872172.
DRY RUN by default. Use --apply to write.
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

def backup_db(db: Path) -> Path:
    bdir = db.parent / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dst = bdir / f"{db.stem}_before_grant_admin_{datetime.now():%Y-%m-%d_%H-%M-%S}{db.suffix}"
    shutil.copy2(db, dst)
    return dst

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--id", type=int, default=1198872172)
    ap.add_argument("--username", default="")
    ap.add_argument("--name", default="Виктория")
    ap.add_argument("--role", default="admin")
    ap.add_argument("--notes", default="Second OSBB administrator")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")

    conn = sqlite3.connect(str(db))
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(bot_admins)")]
        required = {
            "telegram_user_id", "telegram_username", "display_name", "role",
            "can_read", "can_write", "can_manage_users", "can_manage_payments",
            "can_manage_bot", "is_active", "created_at", "updated_at", "notes"
        }
        missing = sorted(required - set(cols))
        if missing:
            raise SystemExit("bot_admins schema mismatch. Missing: " + ", ".join(missing))

        existing = conn.execute(
            "SELECT id FROM bot_admins WHERE telegram_user_id=?",
            (args.id,),
        ).fetchone()

        print("=" * 100)
        print("OSBB grant admin")
        print("=" * 100)
        print("Mode:", "APPLY" if args.apply else "DRY RUN")
        print("DB:", db)
        print("Telegram ID:", args.id)
        print("Name:", args.name)
        print("Role:", args.role)
        print("Existing:", "YES" if existing else "NO")
        print("Permissions: read=1 write=1 manage_users=1 manage_payments=1 manage_bot=0 active=1")

        if not args.apply:
            print("")
            print("DRY RUN COMPLETED. Re-run with --apply to write.")
            return 0

        backup = backup_db(db)
        print("Backup:", backup)

        if existing:
            conn.execute(
                """
                UPDATE bot_admins
                SET telegram_username=?, display_name=?, role=?,
                    can_read=1, can_write=1, can_manage_users=1,
                    can_manage_payments=1, can_manage_bot=0,
                    is_active=1, updated_at=?, notes=?
                WHERE telegram_user_id=?
                """,
                (args.username or None, args.name, args.role, now(), args.notes, args.id),
            )
        else:
            conn.execute(
                """
                INSERT INTO bot_admins (
                    telegram_user_id, telegram_username, display_name, role,
                    can_read, can_write, can_manage_users, can_manage_payments,
                    can_manage_bot, is_active, created_at, updated_at, notes
                )
                VALUES (?, ?, ?, ?, 1, 1, 1, 1, 0, 1, ?, ?, ?)
                """,
                (args.id, args.username or None, args.name, args.role, now(), now(), args.notes),
            )

        conn.commit()

        row = conn.execute(
            """
            SELECT id, telegram_user_id, telegram_username, display_name, role,
                   can_read, can_write, can_manage_users, can_manage_payments,
                   can_manage_bot, is_active, created_at, updated_at, notes
            FROM bot_admins
            WHERE telegram_user_id=?
            """,
            (args.id,),
        ).fetchone()

        print("")
        print("APPLY COMPLETED")
        print("Admin row:")
        print(row)
        return 0
    finally:
        conn.close()

if __name__ == "__main__":
    raise SystemExit(main())
