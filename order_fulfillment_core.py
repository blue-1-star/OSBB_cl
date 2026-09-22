"""Canonical execution layer for every paid OSBB service order.

``service_orders`` remains the commercial document.  A fulfillment records
what is actually delivered: a physical item, a digital access right, or work.
Domain tables (for example ``remote_assets``) remain technical passports and
are linked from this layer; they do not define the general order lifecycle.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any


PHYSICAL_ITEM = "PHYSICAL_ITEM"
DIGITAL_ACCESS = "DIGITAL_ACCESS"
WORK_SERVICE = "WORK_SERVICE"


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_fulfillment_schema(conn: sqlite3.Connection) -> None:
    """Create only additive, generic fulfillment tables (idempotently)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS order_fulfillments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fulfillment_number TEXT NOT NULL UNIQUE,
            service_order_id INTEGER NOT NULL UNIQUE,
            fulfillment_kind TEXT NOT NULL,
            fulfillment_status TEXT NOT NULL DEFAULT 'NOT_STARTED',
            source_location_code TEXT,
            pickup_point_code TEXT,
            recipient_telegram_user_id TEXT,
            recipient_apartment_number TEXT,
            planned_quantity REAL NOT NULL DEFAULT 1,
            prepared_at TEXT,
            handed_over_at TEXT,
            receipt_confirmed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            FOREIGN KEY(service_order_id) REFERENCES service_orders(id)
        );

        CREATE TABLE IF NOT EXISTS order_fulfillment_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fulfillment_id INTEGER NOT NULL,
            item_kind TEXT NOT NULL,
            external_asset_id INTEGER,
            item_label TEXT,
            quantity REAL NOT NULL DEFAULT 1,
            item_status TEXT NOT NULL DEFAULT 'PLANNED',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            FOREIGN KEY(fulfillment_id) REFERENCES order_fulfillments(id)
        );

        CREATE TABLE IF NOT EXISTS order_fulfillment_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fulfillment_id INTEGER NOT NULL,
            fulfillment_item_id INTEGER,
            event_code TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT,
            actor_id TEXT,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(fulfillment_id) REFERENCES order_fulfillments(id),
            FOREIGN KEY(fulfillment_item_id) REFERENCES order_fulfillment_items(id)
        );

        CREATE INDEX IF NOT EXISTS idx_order_fulfillments_status
            ON order_fulfillments(fulfillment_status, fulfillment_kind);
        CREATE INDEX IF NOT EXISTS idx_order_fulfillment_items_external
            ON order_fulfillment_items(item_kind, external_asset_id);
        CREATE INDEX IF NOT EXISTS idx_order_fulfillment_events_fulfillment
            ON order_fulfillment_events(fulfillment_id, id);
        """
    )


def _order(conn: sqlite3.Connection, service_order_id: int) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM service_orders WHERE id=?", (int(service_order_id),)).fetchone()
    if not row:
        raise ValueError(f"Заказ услуги #{service_order_id} не найден.")
    return dict(row)


def _number(conn: sqlite3.Connection) -> str:
    sequence = int(conn.execute("SELECT COUNT(*) FROM order_fulfillments").fetchone()[0]) + 1
    return f"FUL-{datetime.now():%Y%m%d}-{sequence:06d}"


def ensure_order_fulfillment(
    conn: sqlite3.Connection,
    *,
    service_order_id: int,
    fulfillment_kind: str,
    initial_status: str = "NOT_STARTED",
    source_location_code: str | None = None,
    pickup_point_code: str | None = None,
    actor_id: str | int | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Return the one canonical fulfillment for an order, creating it if needed."""
    ensure_fulfillment_schema(conn)
    conn.row_factory = sqlite3.Row
    current = conn.execute(
        "SELECT * FROM order_fulfillments WHERE service_order_id=?", (int(service_order_id),)
    ).fetchone()
    if current:
        return dict(current)

    order = _order(conn, service_order_id)
    timestamp = now_db()
    cur = conn.execute(
        """
        INSERT INTO order_fulfillments(
            fulfillment_number, service_order_id, fulfillment_kind, fulfillment_status,
            source_location_code, pickup_point_code, recipient_telegram_user_id,
            recipient_apartment_number, planned_quantity, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _number(conn), int(service_order_id), fulfillment_kind, initial_status,
            source_location_code, pickup_point_code, order.get("telegram_user_id"),
            order.get("apartment_number"), float(order.get("quantity") or 1), timestamp, timestamp,
        ),
    )
    fulfillment_id = int(cur.lastrowid)
    conn.execute(
        """
        INSERT INTO order_fulfillment_events(
            fulfillment_id, event_code, from_status, to_status, actor_id, note, created_at
        ) VALUES (?, 'FULFILLMENT_CREATED', NULL, ?, ?, ?, ?)
        """,
        (fulfillment_id, initial_status, str(actor_id) if actor_id is not None else "system", note or None, timestamp),
    )
    return dict(conn.execute("SELECT * FROM order_fulfillments WHERE id=?", (fulfillment_id,)).fetchone())


def transition_fulfillment(
    conn: sqlite3.Connection,
    *,
    fulfillment_id: int,
    target_status: str,
    event_code: str,
    actor_id: str | int | None,
    note: str = "",
    pickup_point_code: str | None = None,
) -> dict[str, Any]:
    """Record a state change once, leaving a complete human-readable trail."""
    ensure_fulfillment_schema(conn)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM order_fulfillments WHERE id=?", (int(fulfillment_id),)).fetchone()
    if not row:
        raise ValueError(f"Исполнение #{fulfillment_id} не найдено.")
    current = dict(row)
    timestamp = now_db()
    assignments = ["fulfillment_status=?", "updated_at=?"]
    params: list[Any] = [target_status, timestamp]
    if pickup_point_code is not None:
        assignments.append("pickup_point_code=?")
        params.append(pickup_point_code)
    if target_status == "READY_FOR_PICKUP":
        assignments.append("prepared_at=?")
        params.append(timestamp)
    elif target_status == "HANDED_OVER":
        assignments.append("handed_over_at=?")
        params.append(timestamp)
    elif target_status == "RECEIPT_CONFIRMED":
        assignments.append("receipt_confirmed_at=?")
        params.append(timestamp)
    params.append(int(fulfillment_id))
    conn.execute(f"UPDATE order_fulfillments SET {', '.join(assignments)} WHERE id=?", tuple(params))
    conn.execute(
        """
        INSERT INTO order_fulfillment_events(
            fulfillment_id, event_code, from_status, to_status, actor_id, note, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (int(fulfillment_id), event_code, current["fulfillment_status"], target_status,
         str(actor_id) if actor_id is not None else "system", note or None, timestamp),
    )
    return dict(conn.execute("SELECT * FROM order_fulfillments WHERE id=?", (int(fulfillment_id),)).fetchone())


def attach_fulfillment_item(
    conn: sqlite3.Connection,
    *,
    fulfillment_id: int,
    item_kind: str,
    external_asset_id: int | None,
    item_label: str,
    item_status: str,
    actor_id: str | int | None,
    note: str = "",
) -> int:
    """Attach a concrete asset—or a logical execution item—to the order."""
    ensure_fulfillment_schema(conn)
    existing = conn.execute(
        """
        SELECT id FROM order_fulfillment_items
        WHERE fulfillment_id=? AND item_kind=?
          AND COALESCE(external_asset_id, -1)=COALESCE(?, -1)
        """,
        (int(fulfillment_id), item_kind, external_asset_id),
    ).fetchone()
    if existing:
        return int(existing[0])
    timestamp = now_db()
    cur = conn.execute(
        """
        INSERT INTO order_fulfillment_items(
            fulfillment_id, item_kind, external_asset_id, item_label, item_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (int(fulfillment_id), item_kind, external_asset_id, item_label, item_status, timestamp, timestamp),
    )
    item_id = int(cur.lastrowid)
    conn.execute(
        """
        INSERT INTO order_fulfillment_events(
            fulfillment_id, fulfillment_item_id, event_code, to_status, actor_id, note, created_at
        ) VALUES (?, ?, 'ITEM_ATTACHED', ?, ?, ?, ?)
        """,
        (int(fulfillment_id), item_id, item_status,
         str(actor_id) if actor_id is not None else "system", note or None, timestamp),
    )
    return item_id
