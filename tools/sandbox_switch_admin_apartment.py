#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sandbox_switch_admin_apartment.py

OSBB sandbox helper: temporarily switch the current sandbox admin/resident profile
to another apartment so Telegram scenarios can be tested through the real bot.

Safety:
  - explicit --db required
  - DRY RUN by default
  - writes only with --apply
  - creates DB backup before APPLY
  - intended for sandbox DB only
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


PREFERRED_TELEGRAM_USER_IDS = ["210312208"]


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
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    return {row["name"] for row in cur.execute(f'PRAGMA table_info("{table}")').fetchall()}


def backup_db(db_path: Path) -> Path:
    out_dir = db_path.parent / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{db_path.stem}_before_switch_admin_apartment_{datetime.now():%Y-%m-%d_%H-%M-%S}{db_path.suffix}"
    shutil.copy2(db_path, dst)
    return dst


def find_admin_account(cur: sqlite3.Cursor, telegram_user_id: str = "") -> dict[str, Any] | None:
    if not table_exists(cur, "resident_accounts"):
        return None

    cols = columns(cur, "resident_accounts")

    candidates = []
    if telegram_user_id:
        candidates.append(telegram_user_id)
    candidates.extend(PREFERRED_TELEGRAM_USER_IDS)

    for tid in candidates:
        for col in ["telegram_user_id", "telegram_id", "user_id"]:
            if col in cols:
                row = cur.execute(
                    f'SELECT * FROM resident_accounts WHERE "{col}" = ? ORDER BY id LIMIT 1',
                    (tid,),
                ).fetchone()
                if row:
                    return dict(row)

    n = cur.execute("SELECT COUNT(*) FROM resident_accounts").fetchone()[0]
    if int(n or 0) == 1:
        return dict(cur.execute("SELECT * FROM resident_accounts LIMIT 1").fetchone())

    for col in ["role", "roles", "account_role", "is_admin"]:
        if col not in cols:
            continue
        try:
            sql = (
                f'SELECT * FROM resident_accounts '
                f'WHERE LOWER(COALESCE(CAST("{col}" AS TEXT), "")) LIKE "%admin%" '
                f'   OR CAST("{col}" AS TEXT) = "1" '
                f'ORDER BY id LIMIT 1'
            )
            row = cur.execute(sql).fetchone()
            if row:
                return dict(row)
        except Exception:
            pass

    return None


def apartment_row(cur: sqlite3.Cursor, apartment_id: int, apartment_number: str) -> dict[str, Any] | None:
    if not table_exists(cur, "apartments"):
        return None
    cols = columns(cur, "apartments")
    if "id" in cols:
        row = cur.execute("SELECT * FROM apartments WHERE id = ? LIMIT 1", (int(apartment_id),)).fetchone()
        if row:
            return dict(row)
    if "apartment_number" in cols:
        row = cur.execute("SELECT * FROM apartments WHERE apartment_number = ? LIMIT 1", (apartment_number,)).fetchone()
        if row:
            return dict(row)
    return None


def print_table_row(title: str, row: dict[str, Any] | None) -> None:
    print(title)
    if not row:
        print(" - not found")
        return
    for k, v in row.items():
        print(f" - {k}: {v}")


def update_resident_account(cur: sqlite3.Cursor, account_id: int, apartment_id: int, apartment_number: str) -> list[str]:
    if not table_exists(cur, "resident_accounts"):
        raise RuntimeError("resident_accounts table missing")

    cols = columns(cur, "resident_accounts")
    sets = []
    params: list[Any] = []

    if "apartment_id" in cols:
        sets.append("apartment_id = ?")
        params.append(int(apartment_id))
    if "apartment_number" in cols:
        sets.append("apartment_number = ?")
        params.append(text(apartment_number))
    if "updated_at" in cols:
        sets.append("updated_at = CURRENT_TIMESTAMP")

    if not sets:
        return ["resident_accounts has no apartment_id/apartment_number columns"]

    params.append(int(account_id))
    cur.execute(f"UPDATE resident_accounts SET {', '.join(sets)} WHERE id = ?", params)
    return [f"resident_accounts updated rows={cur.rowcount}"]


def update_links(cur: sqlite3.Cursor, account: dict[str, Any], apartment_id: int, apartment_number: str) -> list[str]:
    messages: list[str] = []
    possible_tables = [
        "resident_apartment_links",
        "resident_unit_links",
        "resident_accounts_apartments",
    ]

    account_id = int(account["id"])
    telegram_user_id = text(account.get("telegram_user_id") or account.get("telegram_id") or account.get("user_id"))

    for table in possible_tables:
        if not table_exists(cur, table):
            continue

        cols = columns(cur, table)
        set_parts = []
        params: list[Any] = []

        if "apartment_id" in cols:
            set_parts.append("apartment_id = ?")
            params.append(int(apartment_id))
        if "apartment_number" in cols:
            set_parts.append("apartment_number = ?")
            params.append(text(apartment_number))
        if "updated_at" in cols:
            set_parts.append("updated_at = CURRENT_TIMESTAMP")

        if not set_parts:
            messages.append(f"{table}: no apartment columns")
            continue

        where_parts = []
        where_params: list[Any] = []

        for col in ["resident_account_id", "account_id"]:
            if col in cols:
                where_parts.append(f"{col} = ?")
                where_params.append(account_id)
                break

        if not where_parts and telegram_user_id:
            for col in ["telegram_user_id", "telegram_id", "user_id"]:
                if col in cols:
                    where_parts.append(f"{col} = ?")
                    where_params.append(telegram_user_id)
                    break

        if not where_parts:
            messages.append(f"{table}: no account link column")
            continue

        cur.execute(
            f"UPDATE {table} SET {', '.join(set_parts)} WHERE {' AND '.join(where_parts)}",
            params + where_params,
        )

        if cur.rowcount == 0:
            insert_cols = []
            insert_vals: list[Any] = []

            if "resident_account_id" in cols:
                insert_cols.append("resident_account_id")
                insert_vals.append(account_id)
            elif "account_id" in cols:
                insert_cols.append("account_id")
                insert_vals.append(account_id)
            elif telegram_user_id and "telegram_user_id" in cols:
                insert_cols.append("telegram_user_id")
                insert_vals.append(telegram_user_id)

            if "apartment_id" in cols:
                insert_cols.append("apartment_id")
                insert_vals.append(int(apartment_id))
            if "apartment_number" in cols:
                insert_cols.append("apartment_number")
                insert_vals.append(text(apartment_number))
            if "is_active" in cols:
                insert_cols.append("is_active")
                insert_vals.append(1)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if "created_at" in cols:
                insert_cols.append("created_at")
                insert_vals.append(now)
            if "updated_at" in cols:
                insert_cols.append("updated_at")
                insert_vals.append(now)

            if insert_cols:
                placeholders = ", ".join("?" for _ in insert_cols)
                cur.execute(
                    f"INSERT INTO {table} ({', '.join(insert_cols)}) VALUES ({placeholders})",
                    insert_vals,
                )
                messages.append(f"{table}: inserted link id={cur.lastrowid}")
            else:
                messages.append(f"{table}: no row updated and insert not possible")
        else:
            messages.append(f"{table}: updated rows={cur.rowcount}")

    if not messages:
        messages.append("no resident-apartment link tables found; resident_accounts only")
    return messages


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Explicit SQLite DB path.")
    ap.add_argument("--apt", required=True, help="Target apartment number.")
    ap.add_argument("--apartment-id", type=int, required=True, help="Target apartment id.")
    ap.add_argument("--telegram-user-id", default="", help="Optional exact telegram user id.")
    ap.add_argument("--apply", action="store_true", help="Apply switch.")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    print("=" * 88)
    print("OSBB sandbox switch admin apartment")
    print("=" * 88)
    print("Mode:", "APPLY" if args.apply else "DRY RUN / READ ONLY")
    print("DB:", db_path)
    print("Target apartment:", args.apt, "id:", args.apartment_id)
    print("")

    conn = connect(db_path, readonly=not args.apply)
    try:
        cur = conn.cursor()

        account = find_admin_account(cur, args.telegram_user_id)
        target_apt = apartment_row(cur, int(args.apartment_id), text(args.apt))

        print_table_row("Admin/resident account before:", account)
        print("")
        print_table_row("Target apartment:", target_apt)
        print("")

        if not account:
            raise SystemExit("Cannot find admin/resident account. Use --telegram-user-id.")
        if not target_apt:
            print("WARNING: target apartment not found in apartments table; continuing would still update account fields.")
            print("")

        if not args.apply:
            print("Planned changes:")
            print(f" - resident account id={account.get('id')} -> apartment_id={args.apartment_id}, apartment_number={args.apt}")
            print(" - update known resident-apartment link table if present")
            print("")
            print("DRY RUN COMPLETED. Re-run with --apply to switch.")
            return 0

        backup = backup_db(db_path)
        messages = []
        messages += update_resident_account(cur, int(account["id"]), int(args.apartment_id), text(args.apt))
        messages += update_links(cur, account, int(args.apartment_id), text(args.apt))
        conn.commit()

        account_after = find_admin_account(
            cur,
            args.telegram_user_id or text(account.get("telegram_user_id") or account.get("telegram_id") or account.get("user_id")),
        )

        print("Backup:", backup)
        print("")
        print("Changes:")
        for m in messages:
            print(" -", m)
        print("")
        print_table_row("Admin/resident account after:", account_after)
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
