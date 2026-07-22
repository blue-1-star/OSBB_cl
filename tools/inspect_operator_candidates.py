#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
inspect_operator_candidates.py

OSBB operator/admin candidates inspector - READ ONLY.

Purpose:
  Show who can be promoted/added as Telegram operator/admin without guessing
  by Telegram ID only.

PowerShell:
  python .\OSBB\tools\inspect_operator_candidates.py --db "G:\Programming\Py\OSBB\Data\db\osbb_test.db"

Optional:
  --apt 174
  --telegram-user-id 210312208
  --out "G:\Programming\Py\OSBB\Data\exports\code\operator_candidates.txt"
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
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


def count_rows(cur: sqlite3.Cursor, table: str) -> int:
    if not table_exists(cur, table):
        return 0
    return int(cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] or 0)


def rows(cur: sqlite3.Cursor, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.execute(sql, params).fetchall()]


def section(out: list[str], title: str) -> None:
    out.append("")
    out.append("=" * 100)
    out.append(title)
    out.append("=" * 100)


def print_rows(out: list[str], data: list[dict[str, Any]]) -> None:
    if not data:
        out.append(" - none")
        return
    for row in data:
        out.append(" - " + repr(row))


def admin_rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    if not table_exists(cur, "bot_admins"):
        return []
    return rows(cur, "SELECT * FROM bot_admins ORDER BY is_active DESC, id")


def resident_rows(cur: sqlite3.Cursor, apt: str = "", telegram_user_id: str = "") -> list[dict[str, Any]]:
    if not table_exists(cur, "resident_accounts"):
        return []

    wh = []
    params: list[Any] = []

    if apt:
        wh.append("apartment_number = ?")
        params.append(apt)
    if telegram_user_id:
        wh.append("CAST(telegram_user_id AS TEXT) = ?")
        params.append(telegram_user_id)

    sql = "SELECT * FROM resident_accounts"
    if wh:
        sql += " WHERE " + " OR ".join(wh)
    sql += " ORDER BY last_seen_at DESC, id LIMIT 100"

    return rows(cur, sql, tuple(params))


def persons_for_apartment(cur: sqlite3.Cursor, apartment_id: Any) -> list[dict[str, Any]]:
    if not table_exists(cur, "persons") or apartment_id is None:
        return []
    return rows(
        cur,
        "SELECT * FROM persons WHERE apartment_id = ? ORDER BY id LIMIT 10",
        (apartment_id,),
    )


def contacts_for_apartment(cur: sqlite3.Cursor, apartment_id: Any) -> list[dict[str, Any]]:
    if not table_exists(cur, "contact_methods") or apartment_id is None:
        return []
    cols = columns(cur, "contact_methods")
    order = "is_primary DESC, id" if "is_primary" in cols else "id"
    return rows(
        cur,
        f"SELECT * FROM contact_methods WHERE apartment_id = ? ORDER BY {order} LIMIT 10",
        (apartment_id,),
    )


def make_candidates(cur: sqlite3.Cursor, apt: str = "", telegram_user_id: str = "") -> list[dict[str, Any]]:
    residents = resident_rows(cur, apt, telegram_user_id)

    admins_by_tid = {}
    for admin in admin_rows(cur):
        tid = text(admin.get("telegram_user_id"))
        if tid:
            admins_by_tid[tid] = admin

    out = []
    for r in residents:
        apt_id = r.get("apartment_id")
        apt_no = text(r.get("apartment_number"))
        persons = persons_for_apartment(cur, apt_id)
        contacts = contacts_for_apartment(cur, apt_id)
        admin = admins_by_tid.get(text(r.get("telegram_user_id")))

        person_names = [text(p.get("full_name")) for p in persons if text(p.get("full_name"))]
        person_phones = [text(p.get("phone_raw")) for p in persons if text(p.get("phone_raw"))]
        contact_values = [text(c.get("contact_value")) for c in contacts if text(c.get("contact_value"))]

        alias_parts = []
        if text(r.get("telegram_username")):
            alias_parts.append("@" + text(r.get("telegram_username")))
        if person_names:
            alias_parts.append(person_names[0])
        if apt_no:
            alias_parts.append("кв." + apt_no)

        alias = " / ".join(alias_parts) or f"telegram:{r.get('telegram_user_id')}"

        out.append(
            {
                "operator_alias": alias,
                "resident_account_id": r.get("id"),
                "telegram_user_id": r.get("telegram_user_id"),
                "telegram_username": r.get("telegram_username"),
                "telegram_name": " ".join(
                    x for x in [text(r.get("telegram_first_name")), text(r.get("telegram_last_name"))] if x
                ),
                "apartment_id": apt_id,
                "apartment_number": apt_no,
                "resident_role": r.get("role"),
                "resident_status": r.get("status"),
                "language_code": r.get("language_code"),
                "person_names": person_names[:5],
                "person_phones": person_phones[:5],
                "contact_values": contact_values[:5],
                "is_bot_admin": bool(admin),
                "bot_admin_role": admin.get("role") if admin else None,
                "bot_admin_can_read": admin.get("can_read") if admin else None,
                "bot_admin_can_write": admin.get("can_write") if admin else None,
                "bot_admin_can_manage_users": admin.get("can_manage_users") if admin else None,
                "bot_admin_can_manage_payments": admin.get("can_manage_payments") if admin else None,
                "bot_admin_can_manage_bot": admin.get("can_manage_bot") if admin else None,
            }
        )
    return out


def build_report(db_path: Path, apt: str, telegram_user_id: str) -> str:
    out: list[str] = []
    conn = connect_ro(db_path)
    try:
        cur = conn.cursor()
        out.append("=" * 100)
        out.append("OSBB operator/admin candidates inspector - READ ONLY")
        out.append("=" * 100)
        out.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
        out.append(f"DB: {db_path}")
        if apt:
            out.append(f"Filter apartment: {apt}")
        if telegram_user_id:
            out.append(f"Filter telegram_user_id: {telegram_user_id}")

        section(out, "TABLE COUNTS")
        for t in ["bot_admins", "resident_accounts", "persons", "contact_methods", "operator_audit_log"]:
            out.append(f" - {t}: {'MISSING' if not table_exists(cur, t) else count_rows(cur, t)}")

        section(out, "BOT ADMINS")
        print_rows(out, admin_rows(cur))

        section(out, "OPERATOR CANDIDATES")
        candidates = make_candidates(cur, apt, telegram_user_id)
        if not candidates:
            out.append(" - none")
        else:
            for c in candidates:
                out.append("")
                out.append(f"Candidate: {c['operator_alias']}")
                for k, v in c.items():
                    out.append(f" - {k}: {v}")

        section(out, "RAW RESIDENT ACCOUNTS")
        print_rows(out, resident_rows(cur, apt, telegram_user_id))

        section(out, "RECOMMENDED NEXT ACTIONS")
        out.append("1. Add operators by readable alias, not by Telegram ID alone.")
        out.append("2. For future chairman/base-review role use CHAIRMAN_REVIEWER or BOARD_REVIEWER.")
        out.append("3. Minimal permissions for this role: view_apartments, view_residents, view_vehicles, verify_apartment_data, edit_contact_basic, edit_vehicle_basic, review_verification_tasks.")
        out.append("4. Keep money/access-activation permissions separate.")
        out.append("5. Log all admin changes in operator_audit_log.")

        out.append("")
        out.append("READ ONLY COMPLETED")
        return "\n".join(out)
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--apt", default="")
    ap.add_argument("--telegram-user-id", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    report = build_report(db_path, args.apt, args.telegram_user_id)
    print(report)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print("")
        print("Output:", out_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
