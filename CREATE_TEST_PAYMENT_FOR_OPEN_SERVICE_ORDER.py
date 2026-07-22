# -*- coding: utf-8 -*-
r"""
Create one clearly marked TEST payment for the current open service order.

This file works only with:
Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db

It never touches the main OSBB database. Before inserting anything it makes a
backup of this sandbox database. It refuses to create a duplicate matching
confirmed payment.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SANDBOX_DIR = ROOT / "Data" / "db" / "sandbox"
BACKUP_DIR = SANDBOX_DIR / "backups"
DB_PATH = SANDBOX_DIR / "osbb_test_live_services_2026-06-26_20-13-26.db"

TEST_PREFIX = "TEST ONLY — service-order payment seed"


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    safe = table_name.replace('"', '""')
    return {
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{safe}")').fetchall()
    }


def backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output = BACKUP_DIR / f"{DB_PATH.stem}_before_test_payment_{stamp}{DB_PATH.suffix}"
    shutil.copy2(DB_PATH, output)
    return output


def main() -> int:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"Sandbox database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        payment_columns = table_columns(conn, "payments")
        order_columns = table_columns(conn, "service_orders")
        required_order = {
            "id", "order_number", "apartment_number", "service_item_code",
            "amount_due_snapshot",
        }
        required_payment = {
            "payment_date", "apartment_number", "amount", "currency",
            "service_item_code",
        }
        missing_order = required_order - order_columns
        missing_payment = required_payment - payment_columns
        if missing_order:
            raise RuntimeError(
                "service_orders schema is incompatible: missing "
                + ", ".join(sorted(missing_order))
            )
        if missing_payment:
            raise RuntimeError(
                "payments schema is incompatible: missing "
                + ", ".join(sorted(missing_payment))
            )

        order = conn.execute(
            """
            SELECT *
            FROM service_orders
            WHERE order_status NOT IN ('COMPLETED', 'CANCELLED')
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        if not order:
            raise RuntimeError("No open service order was found.")

        due = float(order["amount_due_snapshot"] or 0)
        if due <= 0:
            raise RuntimeError(
                "The open order has no positive amount_due_snapshot; "
                "a test payment would be invalid."
            )

        existing = conn.execute(
            """
            SELECT id, amount, payment_date, comment
            FROM payments
            WHERE CAST(apartment_number AS TEXT) = ?
              AND COALESCE(service_item_code, '') = COALESCE(?, '')
              AND COALESCE(cashier_entry_status, 'CONFIRMED') = 'CONFIRMED'
              AND COALESCE(amount, 0) > 0
            ORDER BY id ASC
            """,
            (str(order["apartment_number"]), order["service_item_code"]),
        ).fetchall()
        if existing:
            print("No payment was created: a matching confirmed payment already exists.")
            for row in existing:
                print(
                    f"  id={row['id']} | amount={row['amount']} | "
                    f"date={row['payment_date']} | {row['comment'] or ''}"
                )
            return 0

        fields: list[str] = []
        values: list[object] = []

        def add(name: str, value: object) -> None:
            if name in payment_columns:
                fields.append(name)
                values.append(value)

        today = datetime.now().strftime("%Y-%m-%d")
        comment = f"{TEST_PREFIX}; {order['order_number']}."
        add("payment_date", today)
        add("period_code", None)
        add("apartment_number", str(order["apartment_number"]))
        add("amount", due)
        add("currency", order["currency"] if "currency" in order_columns else "UAH")
        add("payment_method", "cash")
        add("source", "TEST_SERVICE_ORDER_SEED")
        add("created_by", "sandbox_test_seed")
        add("comment", comment)
        add("cashbox_code", "O")
        add("service_item_code", order["service_item_code"])
        add("base_service_code", order["service_item_code"])
        add("service_type", "ONE_TIME")
        add("cashier_entry_status", "CONFIRMED")
        add("payment_channel", "TEST")

        if not fields:
            raise RuntimeError("No writable payment fields were identified.")

        backup_path = backup()
        placeholders = ", ".join("?" for _ in fields)
        quoted = ", ".join('"' + name.replace('"', '""') + '"' for name in fields)
        cur = conn.execute(
            f"INSERT INTO payments ({quoted}) VALUES ({placeholders})",
            tuple(values),
        )
        conn.commit()

        print("TEST payment created.")
        print("Sandbox:", DB_PATH)
        print("Backup:", backup_path)
        print(
            f"Payment id={cur.lastrowid}; order={order['order_number']}; "
            f"apartment={order['apartment_number']}; amount={due:.2f}."
        )
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("TEST PAYMENT CREATION FAILED")
        print(type(exc).__name__ + ":", exc)
        raise SystemExit(1)
