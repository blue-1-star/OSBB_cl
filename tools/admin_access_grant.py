#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
admin_access_grant.py

Simple OSBB access helper:
- add / update a Telegram user for verification work;
- optionally grant admin / service-operator / guard permissions when matching tables exist;
- optionally bind a resident to an apartment/unit if the current DB schema exposes a suitable table;
- dry-run by default;
- creates DB backup before --apply;
- writes a report.

Usage examples:

  python .\OSBB\tools\admin_access_grant.py --inspect

  python .\OSBB\tools\admin_access_grant.py `
    --telegram-id 123456789 `
    --name "Second Admin" `
    --admin `
    --service-operator `
    --guard `
    --apartment 174

  python .\OSBB\tools\admin_access_grant.py `
    --telegram-id 123456789 `
    --name "Second Admin" `
    --admin `
    --service-operator `
    --guard `
    --apartment 174 `
    --apply

Default DB:
  G:\Programming\Py\OSBB\Data\db\osbb_test.db
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
REPORT_DIR = ROOT / "Recovered"

ROLE_TABLE_HINTS = [
    "admin", "admins", "bot_admins", "telegram_admins", "user_roles",
    "service_operator", "service_operators", "service_permissions",
    "guard", "guards", "guard_permissions",
]
USER_TABLE_HINTS = ["telegram_users", "users", "bot_users", "residents", "resident_users"]
APARTMENT_TABLE_HINTS = [
    "apartments", "units", "premises", "resident_units", "unit_users", "user_apartments",
]


@dataclass
class TableInfo:
    name: str
    columns: list[str]


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def tables(con: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]


def columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in con.execute(f"PRAGMA table_info({quote_ident(table)})")]


def find_tables(con: sqlite3.Connection, hints: list[str]) -> list[TableInfo]:
    result = []
    for t in tables(con):
        low = t.lower()
        if any(h in low for h in hints):
            result.append(TableInfo(t, columns(con, t)))
    return result


def has_col(cols: list[str], *names: str) -> str | None:
    low = {c.lower(): c for c in cols}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def select_existing_by_telegram(con: sqlite3.Connection, table: str, cols: list[str], telegram_id: str) -> sqlite3.Row | None:
    tg_col = has_col(cols, "telegram_id", "tg_id", "user_id", "telegram_user_id", "chat_id")
    if not tg_col:
        return None
    rows = con.execute(
        f"SELECT * FROM {quote_ident(table)} WHERE {quote_ident(tg_col)} = ? LIMIT 1",
        (telegram_id,),
    ).fetchall()
    return rows[0] if rows else None


def insert_or_update_user(con: sqlite3.Connection, table: str, cols: list[str], telegram_id: str, name: str, dry: bool, actions: list[str]) -> bool:
    tg_col = has_col(cols, "telegram_id", "tg_id", "user_id", "telegram_user_id", "chat_id")
    if not tg_col:
        return False

    existing = select_existing_by_telegram(con, table, cols, telegram_id)
    name_col = has_col(cols, "name", "full_name", "display_name", "username")
    active_col = has_col(cols, "is_active", "active", "enabled")
    created_col = has_col(cols, "created_at", "created")
    updated_col = has_col(cols, "updated_at", "updated")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if existing:
        sets = []
        params: list[Any] = []
        if name_col and name:
            sets.append(f"{quote_ident(name_col)} = ?")
            params.append(name)
        if active_col:
            sets.append(f"{quote_ident(active_col)} = ?")
            params.append(1)
        if updated_col:
            sets.append(f"{quote_ident(updated_col)} = ?")
            params.append(now)
        if sets:
            sql = f"UPDATE {quote_ident(table)} SET {', '.join(sets)} WHERE {quote_ident(tg_col)} = ?"
            params.append(telegram_id)
            actions.append(f"UPDATE {table}: telegram_id={telegram_id}")
            if not dry:
                con.execute(sql, params)
        else:
            actions.append(f"FOUND {table}: telegram_id={telegram_id}; no writable user fields")
        return True

    insert_cols = [tg_col]
    values: list[Any] = [telegram_id]
    if name_col:
        insert_cols.append(name_col)
        values.append(name)
    if active_col:
        insert_cols.append(active_col)
        values.append(1)
    if created_col:
        insert_cols.append(created_col)
        values.append(now)
    if updated_col:
        insert_cols.append(updated_col)
        values.append(now)

    sql = (
        f"INSERT INTO {quote_ident(table)} "
        f"({', '.join(quote_ident(c) for c in insert_cols)}) VALUES ({','.join(['?'] * len(values))})"
    )
    actions.append(f"INSERT {table}: telegram_id={telegram_id}")
    if not dry:
        con.execute(sql, values)
    return True


def upsert_role_like(con: sqlite3.Connection, table: str, cols: list[str], telegram_id: str, role: str, dry: bool, actions: list[str]) -> bool:
    tg_col = has_col(cols, "telegram_id", "tg_id", "user_id", "telegram_user_id", "chat_id")
    if not tg_col:
        return False

    role_col = has_col(cols, "role", "role_code", "permission", "permission_code", "access_role")
    active_col = has_col(cols, "is_active", "active", "enabled")
    created_col = has_col(cols, "created_at", "created")
    updated_col = has_col(cols, "updated_at", "updated")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if role_col:
        exists = con.execute(
            f"SELECT 1 FROM {quote_ident(table)} WHERE {quote_ident(tg_col)} = ? AND {quote_ident(role_col)} = ? LIMIT 1",
            (telegram_id, role),
        ).fetchone()
    else:
        exists = con.execute(
            f"SELECT 1 FROM {quote_ident(table)} WHERE {quote_ident(tg_col)} = ? LIMIT 1",
            (telegram_id,),
        ).fetchone()

    if exists:
        sets = []
        params: list[Any] = []
        if active_col:
            sets.append(f"{quote_ident(active_col)} = ?")
            params.append(1)
        if updated_col:
            sets.append(f"{quote_ident(updated_col)} = ?")
            params.append(now)
        if sets:
            where = f"{quote_ident(tg_col)} = ?"
            params.append(telegram_id)
            if role_col:
                where += f" AND {quote_ident(role_col)} = ?"
                params.append(role)
            sql = f"UPDATE {quote_ident(table)} SET {', '.join(sets)} WHERE {where}"
            actions.append(f"UPDATE {table}: {telegram_id} role={role}")
            if not dry:
                con.execute(sql, params)
        else:
            actions.append(f"FOUND {table}: {telegram_id} role={role}")
        return True

    insert_cols = [tg_col]
    values: list[Any] = [telegram_id]
    if role_col:
        insert_cols.append(role_col)
        values.append(role)
    if active_col:
        insert_cols.append(active_col)
        values.append(1)
    if created_col:
        insert_cols.append(created_col)
        values.append(now)
    if updated_col:
        insert_cols.append(updated_col)
        values.append(now)

    sql = (
        f"INSERT INTO {quote_ident(table)} "
        f"({', '.join(quote_ident(c) for c in insert_cols)}) VALUES ({','.join(['?'] * len(values))})"
    )
    actions.append(f"INSERT {table}: {telegram_id} role={role}")
    if not dry:
        con.execute(sql, values)
    return True


def role_targets(role: str) -> list[str]:
    if role == "admin":
        return ["admin", "admins", "bot_admins", "telegram_admins", "user_roles", "permissions"]
    if role == "service_operator":
        return ["service_operator", "service_operators", "service_permissions", "user_roles", "permissions"]
    if role == "guard":
        return ["guard", "guards", "guard_permissions", "user_roles", "permissions"]
    if role == "resident":
        return ["resident", "residents", "resident_users", "user_roles", "permissions"]
    return ["user_roles", "permissions"]


def grant_role(con: sqlite3.Connection, telegram_id: str, role: str, dry: bool, actions: list[str]) -> bool:
    matched = False
    for info in find_tables(con, role_targets(role)):
        if role == "admin" and "service" in info.name.lower():
            continue
        if role == "service_operator" and "admin" in info.name.lower():
            continue
        if role == "guard" and "admin" in info.name.lower():
            continue
        if upsert_role_like(con, info.name, info.columns, telegram_id, role.upper(), dry, actions):
            matched = True
    return matched


def bind_apartment(con: sqlite3.Connection, telegram_id: str, apartment: str, dry: bool, actions: list[str]) -> bool:
    matched = False
    for info in find_tables(con, APARTMENT_TABLE_HINTS):
        cols = info.columns
        tg_col = has_col(cols, "telegram_id", "tg_id", "user_id", "telegram_user_id", "chat_id")
        apt_col = has_col(cols, "apartment_number", "unit_number", "premise_code", "unit_code", "apartment", "number")
        active_col = has_col(cols, "is_active", "active", "enabled")
        created_col = has_col(cols, "created_at", "created")
        if not tg_col or not apt_col:
            continue

        exists = con.execute(
            f"SELECT 1 FROM {quote_ident(info.name)} WHERE {quote_ident(tg_col)}=? AND {quote_ident(apt_col)}=? LIMIT 1",
            (telegram_id, apartment),
        ).fetchone()
        if exists:
            actions.append(f"FOUND {info.name}: {telegram_id} apartment={apartment}")
            matched = True
            continue

        insert_cols = [tg_col, apt_col]
        values: list[Any] = [telegram_id, apartment]
        if active_col:
            insert_cols.append(active_col)
            values.append(1)
        if created_col:
            insert_cols.append(created_col)
            values.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        sql = (
            f"INSERT INTO {quote_ident(info.name)} "
            f"({', '.join(quote_ident(c) for c in insert_cols)}) VALUES ({','.join(['?'] * len(values))})"
        )
        actions.append(f"INSERT {info.name}: {telegram_id} apartment={apartment}")
        if not dry:
            con.execute(sql, values)
        matched = True
    return matched


def write_report(db: Path, args: argparse.Namespace, inventory: dict[str, Any], actions: list[str], backup: Path | None, applied: bool) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"ADMIN_ACCESS_GRANT_{now_stamp()}.md"
    lines = []
    lines.append("# Admin access grant report")
    lines.append("")
    lines.append(f"Generated: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`")
    lines.append(f"DB: `{db}`")
    lines.append(f"Applied: `{applied}`")
    if backup:
        lines.append(f"Backup: `{backup}`")
    lines.append("")
    lines.append("## Request")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps({
        "telegram_id": args.telegram_id,
        "name": args.name,
        "admin": args.admin,
        "service_operator": args.service_operator,
        "guard": args.guard,
        "resident": args.resident,
        "apartment": args.apartment,
    }, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## Actions")
    lines.append("")
    if actions:
        for a in actions:
            lines.append(f"- {a}")
    else:
        lines.append("- No actions.")
    lines.append("")
    lines.append("## Inventory")
    lines.append("")
    for key, val in inventory.items():
        lines.append(f"### {key}")
        lines.append("")
        for item in val:
            lines.append(f"- `{item.name}`: {', '.join(item.columns)}")
        if not val:
            lines.append("- none")
        lines.append("")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--telegram-id", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--admin", action="store_true")
    ap.add_argument("--service-operator", action="store_true")
    ap.add_argument("--guard", action="store_true")
    ap.add_argument("--resident", action="store_true")
    ap.add_argument("--apartment", default="")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")

    dry = not args.apply
    actions: list[str] = []
    backup: Path | None = None

    con = connect(db)
    inventory = {
        "user-like tables": find_tables(con, USER_TABLE_HINTS),
        "role-like tables": find_tables(con, ROLE_TABLE_HINTS),
        "apartment-like tables": find_tables(con, APARTMENT_TABLE_HINTS),
    }

    if args.inspect:
        print("=" * 100)
        print("OSBB access DB inspection")
        print("=" * 100)
        print("DB:", db)
        for title, infos in inventory.items():
            print()
            print(title)
            for i in infos:
                print(" -", i.name, ":", ", ".join(i.columns))
        report = write_report(db, args, inventory, actions, None, False)
        print()
        print("Report:", report)
        con.close()
        return 0

    if not args.telegram_id:
        raise SystemExit("Need --telegram-id, or use --inspect.")

    if args.apply:
        backup_dir = ROOT / "Data" / "backups" / "db" / f"before_admin_access_grant_{now_stamp()}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / db.name
        shutil.copy2(db, backup)

    try:
        user_done = False
        for info in inventory["user-like tables"]:
            if insert_or_update_user(con, info.name, info.columns, args.telegram_id, args.name, dry, actions):
                user_done = True
        if not user_done:
            actions.append("WARNING: no writable user-like table found for telegram_id.")

        roles = []
        if args.admin:
            roles.append("admin")
        if args.service_operator:
            roles.append("service_operator")
        if args.guard:
            roles.append("guard")
        if args.resident:
            roles.append("resident")

        for role in roles:
            if not grant_role(con, args.telegram_id, role, dry, actions):
                actions.append(f"WARNING: no writable role-like table found for role={role}.")

        if args.apartment:
            if not bind_apartment(con, args.telegram_id, args.apartment, dry, actions):
                actions.append(f"WARNING: no writable apartment binding table found for apartment={args.apartment}.")

        if args.apply:
            con.commit()
        else:
            con.rollback()
    finally:
        con.close()

    report = write_report(db, args, inventory, actions, backup, args.apply)

    print("=" * 100)
    print("OSBB admin/access grant")
    print("=" * 100)
    print("DB:", db)
    print("Apply:", args.apply)
    if backup:
        print("Backup:", backup)
    print()
    print("Actions:")
    for a in actions:
        print(" -", a)
    print()
    print("Report:", report)
    if not args.apply:
        print()
        print("DRY RUN ONLY - no DB changes saved.")
        print("Add --apply to save changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
