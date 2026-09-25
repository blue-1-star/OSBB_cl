"""Effective supplier minimums and current paid-preorder collection status."""

from __future__ import annotations

import sqlite3

from audit_logger import audit_log
from service_orders_core import get_conn, now_db


def ensure_supplier_terms_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS service_supplier_minimum_history (
            id INTEGER PRIMARY KEY,
            service_item_code TEXT NOT NULL,
            minimum_quantity INTEGER NOT NULL CHECK(minimum_quantity > 0),
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            changed_by TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(valid_to IS NULL OR valid_to >= valid_from)
        )"""
    )
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_service_supplier_minimum_current
           ON service_supplier_minimum_history(service_item_code) WHERE valid_to IS NULL"""
    )


def current_supplier_minimum(service_item_code: str, *,
                             conn: sqlite3.Connection | None = None) -> dict | None:
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_supplier_minimum_history'"
        ).fetchone():
            return None
        row = conn.execute(
            """SELECT * FROM service_supplier_minimum_history
               WHERE service_item_code=? AND valid_to IS NULL ORDER BY id DESC LIMIT 1""",
            (service_item_code.strip().upper(),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        if owns:
            conn.close()


def set_supplier_minimum(*, service_item_code: str, minimum_quantity: int,
                         actor: str, reason: str, conn: sqlite3.Connection | None = None) -> dict:
    code, actor, reason = service_item_code.strip().upper(), actor.strip(), reason.strip()
    quantity = int(minimum_quantity)
    if not code or quantity < 1 or not actor or not reason:
        raise ValueError("Укажите позицию, минимум больше нуля, исполнителя и основание изменения.")
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        if owns:
            conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM service_items WHERE service_item_code=?", (code,)).fetchone():
            raise ValueError("Позиция каталога не найдена.")
        ensure_supplier_terms_schema(conn)
        previous = current_supplier_minimum(code, conn=conn)
        if previous and int(previous["minimum_quantity"]) == quantity:
            if owns:
                conn.commit()
            return previous
        stamp = now_db()
        if previous:
            conn.execute(
                "UPDATE service_supplier_minimum_history SET valid_to=? WHERE id=?",
                (stamp, int(previous["id"])),
            )
        cur = conn.execute(
            """INSERT INTO service_supplier_minimum_history
               (service_item_code,minimum_quantity,valid_from,changed_by,reason,created_at)
               VALUES (?,?,?,?,?,?)""", (code, quantity, stamp, actor, reason, stamp),
        )
        audit_log(
            conn=conn, operator_id=actor, user_id=actor, actor_type="operator",
            action_type="service_supplier_minimum_changed",
            table_name="service_supplier_minimum_history", row_id=cur.lastrowid,
            field_name="minimum_quantity",
            old_value=str(previous["minimum_quantity"]) if previous else "",
            new_value=str(quantity), source_context="supplier_terms_core",
            comment=reason, commit=False,
        )
        result = dict(conn.execute(
            "SELECT * FROM service_supplier_minimum_history WHERE id=?", (int(cur.lastrowid),)
        ).fetchone())
        if owns:
            conn.commit()
        return result
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def paid_preorder_progress(service_item_code: str, *,
                           conn: sqlite3.Connection | None = None) -> dict:
    """Paid orders awaiting supplier-batch assignment, not unverified interests."""
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT COUNT(*) AS order_count,COALESCE(SUM(CAST(o.quantity AS INTEGER)),0) AS quantity
               FROM service_orders o
               JOIN service_order_steps p ON p.service_order_id=o.id
                AND p.step_code='PAYMENT_CONFIRMED' AND p.step_status IN ('CONFIRMED','WAIVED')
               WHERE o.service_item_code=? AND o.order_status NOT IN ('COMPLETED','CANCELLED')
                AND NOT EXISTS (SELECT 1 FROM remote_supplier_batch_links l
                                WHERE l.service_order_id=o.id)""", (service_item_code.strip().upper(),),
        ).fetchone()
        term = current_supplier_minimum(service_item_code, conn=conn)
        return {"quantity": int(row["quantity"]), "order_count": int(row["order_count"]),
                "minimum": int(term["minimum_quantity"]) if term else None,
                "minimum_valid_from": term["valid_from"] if term else None}
    finally:
        if owns:
            conn.close()


def order_preorder_context(order_id: int, *, conn: sqlite3.Connection | None = None) -> dict:
    """Describe either the assigned supplier batch or the current collection pool."""
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT o.service_item_code,b.batch_number,b.batch_status,
                      b.quantity_requested
               FROM service_orders o
               LEFT JOIN remote_supplier_batch_links l ON l.service_order_id=o.id
               LEFT JOIN remote_supplier_batches b ON b.id=l.supplier_batch_id
               WHERE o.id=? ORDER BY l.id DESC LIMIT 1""", (int(order_id),),
        ).fetchone()
        if not row:
            raise ValueError("Заказ не найден.")
        if row["batch_number"]:
            return {"batch_number": row["batch_number"], "batch_status": row["batch_status"],
                    "batch_quantity": int(row["quantity_requested"]), "minimum": None,
                    "quantity": None}
        return {"batch_number": None, "batch_status": None,
                **paid_preorder_progress(row["service_item_code"], conn=conn)}
    finally:
        if owns:
            conn.close()
