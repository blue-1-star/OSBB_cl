#!/usr/bin/env python3
"""Add Telegram recipient snapshots to cash rows; backfill only exact links."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_DB = ROOT / "data" / "db" / "osbb_test.db"
BACKUPS = ROOT / "data" / "db" / "backups"


def apply(conn: sqlite3.Connection) -> dict:
    from cashier_v2_core import ensure_cash_telegram_fields

    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_cash_telegram_fields(cur)
    # A resident-origin notice identifies its Telegram author without guessing.
    for table in ("cashier_receipts", "payments"):
        cur.execute(f"""
            UPDATE {table} SET
              resident_account_id=(SELECT n.resident_account_id FROM payment_notices n
                                   WHERE n.id={table}.payment_notice_id),
              telegram_user_id=(SELECT n.telegram_user_id FROM payment_notices n
                                WHERE n.id={table}.payment_notice_id)
            WHERE payment_notice_id IS NOT NULL AND resident_account_id IS NULL
              AND EXISTS (SELECT 1 FROM payment_notices n
                          WHERE n.id={table}.payment_notice_id)
        """)
    # A confirmed service interest explicitly links its payment to one account.
    for table, payment_key in (("cashier_receipts", "payment_id"), ("payments", "id")):
        cur.execute(f"""
            UPDATE {table} SET
              resident_account_id=(SELECT i.resident_account_id FROM service_order_interests i
                                   WHERE i.payment_id={table}.{payment_key} LIMIT 1),
              telegram_user_id=(SELECT i.telegram_user_id FROM service_order_interests i
                                WHERE i.payment_id={table}.{payment_key} LIMIT 1)
            WHERE resident_account_id IS NULL
              AND EXISTS (SELECT 1 FROM service_order_interests i
                          WHERE i.payment_id={table}.{payment_key}
                            AND i.resident_account_id IS NOT NULL AND i.telegram_user_id IS NOT NULL)
        """)
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE telegram_user_id IS NOT NULL").fetchone()[0]
        for table in ("cashier_receipts", "payments")
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.is_file():
        raise SystemExit(f"DB not found: {db}")
    print("DB:", db)
    if not args.apply:
        print("DRY RUN ONLY; add nullable fields and backfill exact Telegram links.")
        return 0
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"before_2026-09-25_027_cash_telegram_contact_{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(db) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    with sqlite3.connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        counts = apply(conn)
        conn.commit()
    print("APPLIED:", counts, "backup:", backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
