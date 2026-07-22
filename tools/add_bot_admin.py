#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
add_bot_admin.py

OSBB urgent production helper: add/promote a Telegram user to bot_admins.

Purpose:
  Fast practical tool for adding second admin/operator/chairman from existing
  resident_accounts without guessing by Telegram ID only.

Safety:
  - explicit --db required
  - DRY RUN by default
  - --apply writes
  - creates DB backup before APPLY
  - logs APPLY into operator_audit_log when table exists
  - uses current bot_admins schema, no RBAC migration yet

Roles supported by this urgent v1:
  super_admin
  chairman_reviewer
  phone_operator
  cashier
  security
  readonly

PowerShell examples:

  # Show candidates
  python .\OSBB\tools\add_bot_admin.py --db "G:\Programming\Py\OSBB\Data\db\osbb_test.db" --list

  # Dry-run by resident account id
  python .\OSBB\tools\add_bot_admin.py --db "G:\Programming\Py\OSBB\Data\db\osbb_test.db" --resident-account-id 355 --role chairman_reviewer

  # Apply
  python .\OSBB\tools\add_bot_admin.py --db "G:\Programming\Py\OSBB\Data\db\osbb_test.db" --resident-account-id 355 --role chairman_reviewer --apply

  # Or by telegram id
  python .\OSBB\tools\add_bot_admin.py --db "G:\Programming\Py\OSBB\Data\db\osbb_test.db" --telegram-user-id -901782660751 --role chairman_reviewer --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


ROLE_PRESETS = {
    "super_admin": {
        "can_read": 1,
        "can_write": 1,
        "can_manage_users": 1,
        "can_manage_payments": 1,
        "can_manage_bot": 1,
        "notes": "full super admin access",
    },
    "chairman_reviewer": {
        "can_read": 1,
        "can_write": 1,
        "can_manage_users": 0,
        "can_manage_payments": 0,
        "can_manage_bot": 0,
        "notes": "chairman/base data review: apartments, residents, contacts, vehicles, verification",
    },
    "phone_operator": {
        "can_read": 1,
        "can_write": 1,
        "can_manage_users": 0,
        "can_manage_payments": 0,
        "can_manage_bot": 0,
        "notes": "phone access operator",
    },
    "cashier": {
        "can_read": 1,
        "can_write": 1,
        "can_manage_users": 0,
        "can_manage_payments": 1,
        "can_manage_bot": 0,
        "notes": "cashier: accept/confirm payments",
    },
    "security": {
        "can_read": 1,
        "can_write": 1,
        "can_manage_users": 0,
        "can_manage_payments": 0,
        "can_manage_bot": 0,
        "notes": "security limited operator",
    },
    "readonly": {
        "can_read": 1,
        "can_write": 0,
        "can_manage_users": 0,
        "can_manage_payments": 0,
        "can_manage_bot": 0,
        "notes": "read only access",
    },
}


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def connect(db_path: Path, readonly: bool) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    return {r["name"] for r in cur.execute(f'PRAGMA table_info("{table}")').fetchall()}


def backup_db(db_path: Path) -> Path:
    out_dir = db_path.parent / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{db_path.stem}_before_add_bot_admin_{datetime.now():%Y-%m-%d_%H-%M-%S}{db_path.suffix}"
    shutil.copy2(db_path, dst)
    return dst


def persons_for_apartment(cur: sqlite3.Cursor, apartment_id: Any) -> list[dict[str, Any]]:
    if apartment_id is None or not table_exists(cur, "persons"):
        return []
    rows = cur.execute(
        "SELECT * FROM persons WHERE apartment_id = ? ORDER BY id LIMIT 10",
        (apartment_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def make_alias(cur: sqlite3.Cursor, resident: dict[str, Any]) -> str:
    apartment_id = resident.get("apartment_id")
    persons = persons_for_apartment(cur, apartment_id)
    person_names = [text(p.get("full_name")) for p in persons if text(p.get("full_name"))]

    parts = []
    username = text(resident.get("telegram_username"))
    if username:
        parts.append("@" + username)
    if person_names:
        parts.append(person_names[0])
    apt = text(resident.get("apartment_number"))
    if apt:
        parts.append("кв." + apt)
    return " / ".join(parts) or f"telegram:{resident.get('telegram_user_id')}"


def list_candidates(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    if not table_exists(cur, "resident_accounts"):
        return []
    residents = cur.execute(
        """
        SELECT *
        FROM resident_accounts
        ORDER BY last_seen_at DESC, id
        LIMIT 100
        """
    ).fetchall()

    admins = {}
    if table_exists(cur, "bot_admins"):
        for row in cur.execute("SELECT * FROM bot_admins").fetchall():
            admins[text(row["telegram_user_id"])] = dict(row)

    out = []
    for row in residents:
        r = dict(row)
        admin = admins.get(text(r.get("telegram_user_id")))
        out.append(
            {
                "resident_account_id": r.get("id"),
                "telegram_user_id": r.get("telegram_user_id"),
                "telegram_username": r.get("telegram_username"),
                "telegram_name": " ".join(
                    x for x in [text(r.get("telegram_first_name")), text(r.get("telegram_last_name"))] if x
                ),
                "apartment_number": r.get("apartment_number"),
                "status": r.get("status"),
                "alias": make_alias(cur, r),
                "is_bot_admin": bool(admin),
                "bot_admin_role": admin.get("role") if admin else None,
            }
        )
    return out


def find_resident(
    cur: sqlite3.Cursor,
    resident_account_id: int | None,
    telegram_user_id: str,
    apt: str,
) -> dict[str, Any] | None:
    if not table_exists(cur, "resident_accounts"):
        raise RuntimeError("resident_accounts table missing")

    if resident_account_id is not None:
        row = cur.execute(
            "SELECT * FROM resident_accounts WHERE id = ?",
            (resident_account_id,),
        ).fetchone()
        return dict(row) if row else None

    if telegram_user_id:
        row = cur.execute(
            "SELECT * FROM resident_accounts WHERE CAST(telegram_user_id AS TEXT) = ?",
            (telegram_user_id,),
        ).fetchone()
        return dict(row) if row else None

    if apt:
        rows = cur.execute(
            "SELECT * FROM resident_accounts WHERE apartment_number = ? ORDER BY id",
            (apt,),
        ).fetchall()
        if len(rows) == 1:
            return dict(rows[0])
        if len(rows) > 1:
            raise RuntimeError(f"Multiple resident_accounts for apartment {apt}; use --resident-account-id.")

    return None


def existing_admin(cur: sqlite3.Cursor, telegram_user_id: Any) -> dict[str, Any] | None:
    if not table_exists(cur, "bot_admins"):
        return None
    row = cur.execute(
        "SELECT * FROM bot_admins WHERE CAST(telegram_user_id AS TEXT) = ?",
        (text(telegram_user_id),),
    ).fetchone()
    return dict(row) if row else None


def ensure_bot_admins_table(cur: sqlite3.Cursor) -> None:
    if not table_exists(cur, "bot_admins"):
        raise RuntimeError("bot_admins table missing. This urgent tool expects existing bot_admins schema.")


def insert_or_update_admin(
    cur: sqlite3.Cursor,
    resident: dict[str, Any],
    role: str,
    alias: str,
    replace: bool,
) -> tuple[str, dict[str, Any]]:
    ensure_bot_admins_table(cur)
    cols = columns(cur, "bot_admins")
    preset = ROLE_PRESETS[role]
    existing = existing_admin(cur, resident.get("telegram_user_id"))

    values = {
        "telegram_user_id": resident.get("telegram_user_id"),
        "telegram_username": resident.get("telegram_username"),
        "display_name": alias,
        "role": role,
        "can_read": preset["can_read"],
        "can_write": preset["can_write"],
        "can_manage_users": preset["can_manage_users"],
        "can_manage_payments": preset["can_manage_payments"],
        "can_manage_bot": preset["can_manage_bot"],
        "is_active": 1,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "notes": preset["notes"] + f"; alias={alias}; resident_account_id={resident.get('id')}; apt={resident.get('apartment_number')}",
    }

    if existing and not replace:
        return "exists", existing

    if existing and replace:
        set_cols = [c for c in values if c in cols and c != "telegram_user_id"]
        params = [values[c] for c in set_cols]
        params.append(text(resident.get("telegram_user_id")))
        cur.execute(
            f"UPDATE bot_admins SET {', '.join(c + ' = ?' for c in set_cols)} WHERE CAST(telegram_user_id AS TEXT) = ?",
            params,
        )
        return "updated", existing_admin(cur, resident.get("telegram_user_id")) or {}

    insert_cols = [c for c in values if c in cols]
    if "created_at" in cols:
        insert_cols.append("created_at")
        values["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    placeholders = ", ".join("?" for _ in insert_cols)
    cur.execute(
        f"INSERT INTO bot_admins ({', '.join(insert_cols)}) VALUES ({placeholders})",
        [values[c] for c in insert_cols],
    )
    return "inserted", existing_admin(cur, resident.get("telegram_user_id")) or {}


def log_operator_audit(
    cur: sqlite3.Cursor,
    *,
    actor_operator_id: str,
    action_type: str,
    table_name: str,
    row_id: str,
    comment: str,
    extra: dict[str, Any],
) -> None:
    if not table_exists(cur, "operator_audit_log"):
        return

    cols = columns(cur, "operator_audit_log")
    data = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "operator_id": actor_operator_id,
        "user_id": actor_operator_id,
        "actor_type": "super_admin_tool",
        "action_type": action_type,
        "table_name": table_name,
        "row_id": row_id,
        "field_name": None,
        "old_value": None,
        "new_value": None,
        "action_status": "applied",
        "review_status": "not_required",
        "source_context": "tools/add_bot_admin.py",
        "comment": comment,
        "db_mode": "direct_sqlite_tool",
        "db_file": None,
        "extra_json": json.dumps(extra, ensure_ascii=False),
    }

    insert_cols = [c for c in data if c in cols]
    placeholders = ", ".join("?" for _ in insert_cols)
    cur.execute(
        f"INSERT INTO operator_audit_log ({', '.join(insert_cols)}) VALUES ({placeholders})",
        [data[c] for c in insert_cols],
    )


def print_candidate(row: dict[str, Any]) -> None:
    print(
        f" - resident_account_id={row['resident_account_id']} | "
        f"telegram={row['telegram_user_id']} | "
        f"{row['alias']} | "
        f"admin={row['is_bot_admin']} role={row['bot_admin_role']}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--list", action="store_true", help="List candidate resident_accounts.")
    ap.add_argument("--resident-account-id", type=int, default=None)
    ap.add_argument("--telegram-user-id", default="")
    ap.add_argument("--apt", default="")
    ap.add_argument("--role", choices=sorted(ROLE_PRESETS), default="chairman_reviewer")
    ap.add_argument("--replace", action="store_true", help="Update existing bot_admin if already present.")
    ap.add_argument("--actor-operator-id", default="tool", help="Who performs this admin change.")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    print("=" * 100)
    print("OSBB add/promote bot admin")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN / READ ONLY")
    print("DB:", db_path)
    print("Role:", args.role)
    print("")

    conn = connect(db_path, readonly=not args.apply)
    try:
        cur = conn.cursor()

        if args.list:
            print("Candidates:")
            candidates = list_candidates(cur)
            if not candidates:
                print(" - none")
            else:
                for c in candidates:
                    print_candidate(c)
            print("")
            print("READ ONLY COMPLETED")
            return 0

        resident = find_resident(cur, args.resident_account_id, args.telegram_user_id, args.apt)
        if not resident:
            print("Resident account not found.")
            print("Use --list to see candidates.")
            return 2

        alias = make_alias(cur, resident)
        current_admin = existing_admin(cur, resident.get("telegram_user_id"))

        print("Selected resident:")
        for k, v in resident.items():
            print(f" - {k}: {v}")
        print("Alias:", alias)
        print("")
        print("Current bot_admin:")
        if current_admin:
            for k, v in current_admin.items():
                print(f" - {k}: {v}")
        else:
            print(" - none")
        print("")
        print("Planned action:")
        if current_admin and not args.replace:
            print(" - existing bot_admin found; no write unless --replace is used")
        elif current_admin and args.replace:
            print(f" - update existing bot_admin to role={args.role}")
        else:
            print(f" - insert bot_admin role={args.role}")
        print(" - write operator_audit_log event if table exists")
        print("")

        if not args.apply:
            print("DRY RUN COMPLETED. Re-run with --apply to write.")
            return 0

        backup = backup_db(db_path)

        status, admin_after = insert_or_update_admin(
            cur=cur,
            resident=resident,
            role=args.role,
            alias=alias,
            replace=args.replace,
        )

        log_operator_audit(
            cur,
            actor_operator_id=args.actor_operator_id,
            action_type="BOT_ADMIN_" + status.upper(),
            table_name="bot_admins",
            row_id=text(admin_after.get("id")),
            comment=f"{status} bot admin {alias} as {args.role}",
            extra={
                "status": status,
                "role": args.role,
                "alias": alias,
                "resident_account_id": resident.get("id"),
                "telegram_user_id": resident.get("telegram_user_id"),
                "apartment_number": resident.get("apartment_number"),
            },
        )

        conn.commit()

        print("Backup:", backup)
        print("Result:", status)
        print("")
        print("bot_admin after:")
        for k, v in admin_after.items():
            print(f" - {k}: {v}")
        print("")
        print("APPLY COMPLETED")
        return 0

    except Exception:
        if args.apply:
            conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
