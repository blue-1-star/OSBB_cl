#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
working_bind_user_to_apartment.py

TEMPORARY TEST TOOL.

Purpose:
- in a disposable working DB, force-bind one Telegram user to one apartment;
- use this only for acceptance tests like debt blocking / pult order / phone access;
- do NOT use on Golden Master or production DB.

Typical use:

  python .\OSBB\tools\working_bind_user_to_apartment.py --db "G:\Programming\Py\OSBB\Data\db\working\osbb_working_pult_order_test_2026-07-02_17-18-39.db" --telegram-id 210312208 --apartment 89

Then, if dry-run looks right:

  python .\OSBB\tools\working_bind_user_to_apartment.py --db "G:\Programming\Py\OSBB\Data\db\working\osbb_working_pult_order_test_2026-07-02_17-18-39.db" --telegram-id 210312208 --apartment 89 --apply

Rollback is simple:
- delete the whole working DB,
- or create a fresh working DB from osbb_test.db.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def q(conn: sqlite3.Connection, sql: str, params=()):
    return conn.execute(sql, params)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return q(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in q(conn, f"PRAGMA table_info({table})").fetchall()]


def find_apartment(conn: sqlite3.Connection, apartment: str):
    if not table_exists(conn, "apartments"):
        raise SystemExit("No apartments table")

    cols = columns(conn, "apartments")
    number_cols = [c for c in ("number", "apartment_number", "apt_number", "flat_number") if c in cols]

    if not number_cols:
        raise SystemExit(f"Cannot find apartment number column in apartments. Columns: {cols}")

    col = number_cols[0]
    row = q(conn, f"SELECT * FROM apartments WHERE {col} = ?", (apartment,)).fetchone()
    if not row:
        row = q(conn, f"SELECT * FROM apartments WHERE CAST({col} AS TEXT) = ?", (str(apartment),)).fetchone()
    if not row:
        raise SystemExit(f"Apartment not found: {apartment}")

    data = dict(zip(cols, row))
    apt_id = data.get("id")
    return apt_id, data, col


def detect_user_tables(conn: sqlite3.Connection) -> list[str]:
    candidates = []
    for table in ("resident_accounts", "residents", "users", "telegram_users", "bot_users"):
        if table_exists(conn, table):
            cols = columns(conn, table)
            if "telegram_user_id" in cols or "telegram_id" in cols:
                candidates.append(table)
    return candidates


def find_user_rows(conn: sqlite3.Connection, telegram_id: int):
    found = []
    for table in detect_user_tables(conn):
        cols = columns(conn, table)
        tg_col = "telegram_user_id" if "telegram_user_id" in cols else "telegram_id"
        rows = q(conn, f"SELECT * FROM {table} WHERE {tg_col} = ?", (telegram_id,)).fetchall()
        for row in rows:
            found.append((table, cols, dict(zip(cols, row))))
    return found


def update_user_table(conn: sqlite3.Connection, table: str, telegram_id: int, apt_id: int, apply: bool):
    cols = columns(conn, table)
    tg_col = "telegram_user_id" if "telegram_user_id" in cols else "telegram_id"

    sets = []
    params = []

    for c in ("apartment_id", "flat_id"):
        if c in cols:
            sets.append(f"{c} = ?")
            params.append(apt_id)

    for c in ("is_verified", "verified", "is_approved", "approved"):
        if c in cols:
            sets.append(f"{c} = 1")

    for c in ("status", "verification_status"):
        if c in cols:
            sets.append(f"{c} = ?")
            params.append("verified")

    for c in ("updated_at", "modified_at"):
        if c in cols:
            sets.append(f"{c} = ?")
            params.append(now())

    if not sets:
        return False, f"No writable apartment/verification columns found in {table}"

    params.append(telegram_id)
    sql = f"UPDATE {table} SET {', '.join(sets)} WHERE {tg_col} = ?"

    print("Planned SQL:")
    print(" ", sql)
    print("Params:", params)

    if apply:
        q(conn, sql, params)
    return True, "updated"


def insert_audit(conn: sqlite3.Connection, telegram_id: int, apartment: str, apt_id: int, apply: bool):
    message = f"TEST ONLY: forced telegram_id={telegram_id} to apartment={apartment} apartment_id={apt_id}"

    for table in ("operator_audit_log", "audit_log"):
        if not table_exists(conn, table):
            continue
        cols = columns(conn, table)
        data = {}
        for c in cols:
            if c == "created_at":
                data[c] = now()
            elif c in ("event_type", "action", "operation"):
                data[c] = "test_force_bind"
            elif c in ("actor_telegram_user_id", "telegram_user_id", "operator_telegram_user_id"):
                data[c] = telegram_id
            elif c in ("details", "message", "notes", "description"):
                data[c] = message
        if data:
            sql = f"INSERT INTO {table} ({', '.join(data.keys())}) VALUES ({', '.join(['?'] * len(data))})"
            print("Audit SQL:", sql)
            if apply:
                q(conn, sql, list(data.values()))
            return


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--telegram-id", type=int, required=True)
    ap.add_argument("--apartment", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")

    low = str(db).lower().replace("/", "\\")
    if "\\working\\" not in low:
        raise SystemExit("Safety stop: DB path does not look like a working DB. Refusing.")

    print("=" * 100)
    print("OSBB working DB force-bind user to apartment")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("DB:", db)
    print("Telegram ID:", args.telegram_id)
    print("Apartment:", args.apartment)

    with sqlite3.connect(db) as conn:
        apt_id, apt_data, apt_number_col = find_apartment(conn, args.apartment)
        print("")
        print("Apartment found:")
        print(" id:", apt_id)
        print(" number column:", apt_number_col)
        print(" data:", apt_data)

        rows = find_user_rows(conn, args.telegram_id)
        print("")
        print("Existing user rows:")
        if not rows:
            print(" none")
        for table, cols, row in rows:
            print(" table:", table)
            print(" row:", row)

        if not rows:
            raise SystemExit("No user row found for telegram id. Cannot safely bind.")

        print("")
        changed_any = False
        for table, cols, row in rows:
            ok, msg = update_user_table(conn, table, args.telegram_id, apt_id, args.apply)
            print(f"{table}: {msg}")
            changed_any = changed_any or ok

        insert_audit(conn, args.telegram_id, args.apartment, apt_id, args.apply)

        if args.apply and changed_any:
            conn.commit()
        else:
            conn.rollback()

    print("")
    if args.apply:
        print("APPLY COMPLETED")
    else:
        print("DRY RUN COMPLETED. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
