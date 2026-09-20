#!/usr/bin/env python3
"""Create the OSBB-wide read-only role without assigning it to anyone.

The role is built from every currently registered VIEW operation in the RBAC
schema.  It deliberately contains no CREATE, UPDATE, DELETE, MANAGE, CONFIRM,
ISSUE, MOVE, ACTIVATE or ENTER permission.  Run without --apply first.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUP_DIR = ROOT / "Data" / "db" / "backups"
ROLE_CODE = "OSBB_READ_ONLY"


def backup_database(source: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"before_2026-09-20_014_osbb_read_only_{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(source, target)
    return target


def view_resources(cur: sqlite3.Cursor) -> list[str]:
    resources = {
        row[0]
        for row in cur.execute("SELECT DISTINCT resource FROM access_role_permissions WHERE action='VIEW'")
    }
    resources.update(
        row[0].split(".", 1)[0]
        for row in cur.execute("SELECT permission_code FROM access_permissions WHERE permission_code LIKE '%.VIEW'")
    )
    return sorted(resources)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")

    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        resources = view_resources(cur)
        print(f"DB: {db}")
        print(f"Role: {ROLE_CODE}")
        print("VIEW resources:", ", ".join(resources) or "none")
        if not resources:
            raise RuntimeError("No registered VIEW resources; refusing to create an empty role.")
        if not args.apply:
            print("DRY RUN ONLY — no changes saved. Use --apply to create the role.")
            return 0
    finally:
        conn.close()

    backup = backup_database(db)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        resources = view_resources(cur)
        cur.execute(
            """
            INSERT INTO access_roles(role_code, role_name, description, is_active, created_at, updated_at)
            VALUES (?, 'Наблюдатель ОСББ — только чтение', ?, 1, ?, ?)
            ON CONFLICT(role_code) DO UPDATE SET role_name=excluded.role_name,
                description=excluded.description, is_active=1, updated_at=excluded.updated_at
            """,
            (
                ROLE_CODE,
                "Просмотр всех зарегистрированных ресурсов ACL. Без создания, изменения, подтверждения, выдачи и управления правами.",
                timestamp, timestamp,
            ),
        )
        for resource in resources:
            permission_code = f"{resource}.VIEW"
            cur.execute(
                """
                INSERT INTO access_permissions(permission_code, permission_name, category, description, is_active, created_at, updated_at)
                VALUES (?, ?, 'READ_ONLY', ?, 1, ?, ?)
                ON CONFLICT(permission_code) DO UPDATE SET is_active=1, updated_at=excluded.updated_at
                """,
                (permission_code, permission_code, f"Просмотр ресурса {resource}.", timestamp, timestamp),
            )
            cur.execute(
                """
                INSERT INTO access_role_permissions(role_code, resource, action, scope_type, scope_value,
                    effect, is_active, note, created_at, updated_at)
                VALUES (?, ?, 'VIEW', 'ALL', '*', 'ALLOW', 1, ?, ?, ?)
                ON CONFLICT(role_code, resource, action, scope_type, scope_value) DO UPDATE SET
                    effect='ALLOW', is_active=1, note=excluded.note, updated_at=excluded.updated_at
                """,
                (ROLE_CODE, resource, "OSBB-wide read-only role.", timestamp, timestamp),
            )
        cur.execute(
            """
            INSERT INTO access_audit_log(created_at, actor_telegram_user_id, action_type, resource,
                action, scope_type, scope_value, target_table, target_id, success, details)
            VALUES (?, 'system', 'role_created', 'access_roles', 'CREATE', 'ALL', '*',
                    'access_roles', ?, 1, ?)
            """,
            (timestamp, ROLE_CODE, f"Created {ROLE_CODE} with {len(resources)} VIEW-only rules; no user assignment."),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"APPLIED. Role {ROLE_CODE}; VIEW rules: {len(resources)}; no users assigned.")
    print(f"Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
