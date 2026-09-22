#!/usr/bin/env python3
"""Register SUPER_ADMIN as an auditable global RBAC role.

Existing active ``bot_admins.role='super_admin'`` records are migrated to the
new role assignment.  No Telegram secret or hard-coded ID is read here.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUPS = ROOT / "Data" / "db" / "backups"
MIGRATION = "2026-09-22_020_register_super_admin_rbac"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")
    with sqlite3.connect(db) as conn:
        candidates = conn.execute(
            "SELECT telegram_user_id FROM bot_admins WHERE role='super_admin' AND COALESCE(is_active,1)=1"
        ).fetchall()
    print(f"DB: {db}")
    print("Super-admin records to migrate:", ", ".join(str(r[0]) for r in candidates) or "none")
    if not args.apply:
        print("DRY RUN ONLY — no changes saved.")
        return 0

    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"before_{MIGRATION}_{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(db, backup)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db) as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO access_roles(role_code, role_name, description, is_active, created_at, updated_at)
               VALUES ('SUPER_ADMIN', 'Супер-администратор',
                       'Глобальная аварийная и административная роль RBAC.', 1, ?, ?)
               ON CONFLICT(role_code) DO UPDATE SET is_active=1, updated_at=excluded.updated_at""",
            (now, now),
        )
        assigned = 0
        for (user_id,) in candidates:
            cur.execute(
                """INSERT INTO access_user_roles(telegram_user_id, role_code, scope_type, scope_value,
                       is_active, valid_from, valid_to, granted_by, note, created_at, updated_at)
                   VALUES (?, 'SUPER_ADMIN', 'ALL', '*', 1, ?, NULL, 'migration', ?, ?, ?)
                   ON CONFLICT(telegram_user_id, role_code, scope_type, scope_value) DO UPDATE SET
                       is_active=1, valid_to=NULL, granted_by='migration',
                       note=excluded.note, updated_at=excluded.updated_at""",
                (str(user_id), now, "Перенесено из bot_admins.super_admin", now, now),
            )
            cur.execute(
                """INSERT INTO access_audit_log(created_at, actor_telegram_user_id, action_type,
                       resource, action, scope_type, scope_value, target_table, target_id, success, details)
                   VALUES (?, 'migration', 'super_admin_rbac_granted', 'access_user_roles', 'GRANT',
                           'ALL', '*', 'access_user_roles', ?, 1, ?)""",
                (now, str(user_id), "SUPER_ADMIN перенесён из bot_admins; глобальный RBAC-доступ."),
            )
            assigned += 1
        conn.commit()
    print(f"APPLIED. SUPER_ADMIN role active; assignments migrated: {assigned}. Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
