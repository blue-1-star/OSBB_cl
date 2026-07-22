# -*- coding: utf-8 -*-
r"""
OSBB — one-time compatibility repair for the LIVE SERVICES sandbox database.

This script is intentionally limited to ONE database:
  Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db

It does not touch:
  - Data\db\osbb.db
  - any other sandbox database
  - any Python source file
  - config.py

It does:
  1. Creates a timestamped backup of this sandbox database.
  2. Adds payments.apartment_id when the old payments schema does not have it.
  3. Adds payments.source_ref when absent for compatibility with older UI code.
  4. Resolves apartment_id from apartment_number, first via apartments and then
     via service_orders.
  5. Verifies the payment selected in the test workflow (#93) belongs to the
     same apartment as SO-20260626-000001.

Run only while the LIVE SERVICES sandbox bot is stopped.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import traceback
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = (
    ROOT
    / "Data"
    / "db"
    / "sandbox"
    / "osbb_test_live_services_2026-06-26_20-13-26.db"
)
BACKUP_DIR = ROOT / "Data" / "db" / "sandbox" / "backups"
LOG_DIR = ROOT / "Data" / "db" / "logs"

TEST_ORDER_NUMBER = "SO-20260626-000001"
TEST_PAYMENT_ID = 93

OUTPUT: list[str] = []


def emit(message: object = "") -> None:
    line = str(message)
    print(line)
    OUTPUT.append(line)


def quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({quote(table)})").fetchall()
    }


def add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> bool:
    current = columns(conn, table)
    if column in current:
        emit(f"OK: {table}.{column} already exists.")
        return False

    conn.execute(
        f"ALTER TABLE {quote(table)} "
        f"ADD COLUMN {quote(column)} {definition}"
    )
    if column not in columns(conn, table):
        raise RuntimeError(f"Column was not added: {table}.{column}")
    emit(f"ADDED: {table}.{column} {definition}")
    return True


def backup_database() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"{DB_PATH.stem}_before_payment_schema_fix_{stamp}{DB_PATH.suffix}"
    shutil.copy2(DB_PATH, backup_path)
    emit(f"Backup created: {backup_path}")
    return backup_path


def map_from_apartments(conn: sqlite3.Connection) -> int:
    """
    Populate payments.apartment_id from apartments.id for matching apartment_number.
    The project uses string apartment identifiers, so the comparison remains text.
    """
    if not table_exists(conn, "apartments"):
        emit("INFO: table apartments is absent; skip primary apartment mapping.")
        return 0

    apartment_columns = columns(conn, "apartments")
    payment_columns = columns(conn, "payments")
    if not {"id", "apartment_number"}.issubset(apartment_columns):
        emit("INFO: apartments lacks id/apartment_number; skip primary apartment mapping.")
        return 0
    if not {"apartment_id", "apartment_number"}.issubset(payment_columns):
        raise RuntimeError("payments needs apartment_id and apartment_number for mapping.")

    sql = """
        UPDATE payments AS p
        SET apartment_id = (
            SELECT a.id
            FROM apartments AS a
            WHERE TRIM(CAST(a.apartment_number AS TEXT))
                  = TRIM(CAST(p.apartment_number AS TEXT))
            ORDER BY a.id
            LIMIT 1
        )
        WHERE p.apartment_id IS NULL
          AND p.apartment_number IS NOT NULL
          AND TRIM(CAST(p.apartment_number AS TEXT)) <> ''
          AND EXISTS (
              SELECT 1
              FROM apartments AS a
              WHERE TRIM(CAST(a.apartment_number AS TEXT))
                    = TRIM(CAST(p.apartment_number AS TEXT))
          )
    """
    before = conn.total_changes
    conn.execute(sql)
    changed = conn.total_changes - before
    emit(f"Mapped via apartments: {changed} payment row(s).")
    return changed


def map_from_service_orders(conn: sqlite3.Connection) -> int:
    """
    Fallback mapping for a service-order sandbox: use service_orders itself.
    This specifically covers a test payment for an existing service order.
    """
    if not table_exists(conn, "service_orders"):
        emit("INFO: table service_orders is absent; skip fallback mapping.")
        return 0

    order_columns = columns(conn, "service_orders")
    payment_columns = columns(conn, "payments")
    required_orders = {"id", "apartment_id", "apartment_number"}
    required_payments = {"apartment_id", "apartment_number"}
    if not required_orders.issubset(order_columns) or not required_payments.issubset(payment_columns):
        emit("INFO: required apartment columns are absent; skip fallback service-order mapping.")
        return 0

    sql = """
        UPDATE payments AS p
        SET apartment_id = (
            SELECT o.apartment_id
            FROM service_orders AS o
            WHERE o.apartment_id IS NOT NULL
              AND TRIM(CAST(o.apartment_number AS TEXT))
                    = TRIM(CAST(p.apartment_number AS TEXT))
            ORDER BY o.id DESC
            LIMIT 1
        )
        WHERE p.apartment_id IS NULL
          AND p.apartment_number IS NOT NULL
          AND TRIM(CAST(p.apartment_number AS TEXT)) <> ''
          AND EXISTS (
              SELECT 1
              FROM service_orders AS o
              WHERE o.apartment_id IS NOT NULL
                AND TRIM(CAST(o.apartment_number AS TEXT))
                      = TRIM(CAST(p.apartment_number AS TEXT))
          )
    """
    before = conn.total_changes
    conn.execute(sql)
    changed = conn.total_changes - before
    emit(f"Mapped via service_orders fallback: {changed} payment row(s).")
    return changed


def verify_test_workflow(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "service_orders"):
        raise RuntimeError("Missing service_orders; wrong database selected.")

    order = conn.execute(
        """
        SELECT id, order_number, apartment_id, apartment_number, payment_status
        FROM service_orders
        WHERE order_number = ?
        """,
        (TEST_ORDER_NUMBER,),
    ).fetchone()

    if order is None:
        raise RuntimeError(
            f"Test order {TEST_ORDER_NUMBER} was not found in the selected sandbox."
        )

    payment_columns = columns(conn, "payments")
    if "apartment_id" not in payment_columns:
        raise RuntimeError("payments.apartment_id still does not exist after repair.")

    wanted_payment_columns = [
        "id", "apartment_id", "apartment_number", "amount",
        "cashbox_code", "service_item_code",
    ]
    available_payment_columns = columns(conn, "payments")
    selected_payment_columns = [
        column for column in wanted_payment_columns
        if column in available_payment_columns
    ]
    payment = conn.execute(
        "SELECT "
        + ", ".join(quote(column) for column in selected_payment_columns)
        + " FROM payments WHERE id = ?",
        (TEST_PAYMENT_ID,),
    ).fetchone()

    if payment is None:
        raise RuntimeError(
            f"Test payment #{TEST_PAYMENT_ID} was not found in the selected sandbox."
        )

    emit("")
    emit("Verification of the exact test workflow:")
    emit(
        f"Order {order['order_number']}: "
        f"apartment_id={order['apartment_id']!r}, "
        f"apartment_number={order['apartment_number']!r}, "
        f"payment_status={order['payment_status']!r}"
    )
    emit(
        f"Payment #{payment['id']}: "
        f"apartment_id={payment['apartment_id']!r}, "
        f"apartment_number={payment['apartment_number']!r}, "
        f"amount={payment['amount']!r}, "
        f"cashbox={payment['cashbox_code'] if 'cashbox_code' in payment.keys() else None!r}, "
        f"service={payment['service_item_code'] if 'service_item_code' in payment.keys() else None!r}"
    )

    if order["apartment_id"] is None:
        raise RuntimeError("The test order itself has no apartment_id.")
    if payment["apartment_id"] is None:
        raise RuntimeError(
            f"Payment #{TEST_PAYMENT_ID} could not be mapped to an apartment_id."
        )
    if int(order["apartment_id"]) != int(payment["apartment_id"]):
        raise RuntimeError(
            "Safety stop: selected payment and test order point to different apartments."
        )

    emit("PASS: selected payment and test order have the same apartment_id.")


def write_log() -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = LOG_DIR / f"live_services_payment_schema_fix_{stamp}.txt"
        log_path.write_text("\n".join(OUTPUT) + "\n", encoding="utf-8")
        print(f"\nLog: {log_path}")
    except Exception as exc:  # do not hide the real outcome
        print(f"\nCould not write log: {exc}")


def main() -> int:
    emit("OSBB LIVE SERVICES SANDBOX — PAYMENT SCHEMA FIX")
    emit("=" * 92)
    emit(f"Database: {DB_PATH}")

    if not DB_PATH.is_file():
        raise FileNotFoundError(
            "The expected live-services sandbox database was not found. "
            "No file was changed."
        )

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        if not table_exists(conn, "payments"):
            raise RuntimeError("Wrong database: table payments is missing.")
        if not table_exists(conn, "service_orders"):
            raise RuntimeError(
                "Wrong database: table service_orders is missing. "
                "This repair must not run against the old guard sandbox."
            )

        backup_database()

        try:
            conn.execute("BEGIN IMMEDIATE")
            add_column_if_missing(conn, "payments", "apartment_id", "INTEGER")
            add_column_if_missing(conn, "payments", "source_ref", "TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_payments_apartment_id
                ON payments(apartment_id)
                """
            )
            map_from_apartments(conn)
            map_from_service_orders(conn)
            verify_test_workflow(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    emit("")
    emit("DONE: the sandbox payment schema is compatible with the service-order link.")
    emit("Now restart Start_OSBB_Live_Services_Sandbox_Bot_v1.bat and repeat payment #93.")
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as error:  # noqa: BLE001
        emit("")
        emit("FAILED: " + str(error))
        write_log()
        sys.exit(1)
    else:
        write_log()
        sys.exit(exit_code)
