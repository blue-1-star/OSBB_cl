# -*- coding: utf-8 -*-
"""
Simplified paid-service workflow for OSBB remotes and phone access.

Business rules implemented here:
- an unpaid resident request is an interest record, not an operational order;
- a confirmed payment promotes one matching interest into a real service order;
- new remotes are paid preorders, aggregated into supplier batches;
- no individual remote is reserved before a supplier delivery exists;
- a physical remote asset is created only when it is actually issued from a
  received supplier batch;
- existing service_orders stay immutable historical records.

This module is deliberately independent from the cashier implementation. It
uses payment_notices/payment rows already created by the cashier subsystem and
reconciles them safely by payment_notice_id whenever possible.
"""

from __future__ import annotations

from datetime import datetime
import sqlite3
from typing import Any

from service_orders_core import (
    confirm_order_step,
    create_remote_asset,
    create_service_order,
    effective_price,
    get_conn,
    get_service_order,
    get_service_workflow,
    link_payment_to_order,
    now_db,
    record_remote_movement,
    table_columns,
    table_exists,
    text,
)


INTEREST = "INTEREST"
PAYMENT_NOTICE = "PAYMENT_NOTICE"
PAID_ORDER_CREATED = "PAID_ORDER_CREATED"
CANCELLED = "CANCELLED"

BATCH_DRAFT = "DRAFT"
BATCH_ORDERED = "ORDERED"
BATCH_PARTIAL = "PARTIAL"
BATCH_RECEIVED = "RECEIVED"
BATCH_CLOSED = "CLOSED"

LINK_PLANNED = "PLANNED"
LINK_READY = "READY"
LINK_ISSUED = "ISSUED"

NEW_REMOTE_OLD_PROFILE = "REMOTE_NEW_FROM_STOCK"
NEW_REMOTE_PROFILE = "REMOTE_NEW_PREORDER"


def _dict(cur: sqlite3.Cursor, sql: str, params: tuple[Any, ...] = ()) -> dict | None:
    cur.execute(sql, params)
    row = cur.fetchone()
    return dict(row) if row else None


def _rows(cur: sqlite3.Cursor, sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
    cur.execute(sql, params)
    return [dict(row) for row in cur.fetchall()]


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _number(prefix: str, cur: sqlite3.Cursor, table: str, field: str) -> str:
    day = datetime.now().strftime("%Y%m%d")
    value_prefix = f"{prefix}-{day}-"
    cur.execute(
        f"SELECT {_quote(field)} FROM {_quote(table)} "
        f"WHERE {_quote(field)} LIKE ? ORDER BY {_quote(field)} DESC LIMIT 1",
        (value_prefix + "%",),
    )
    row = cur.fetchone()
    serial = 1
    if row:
        try:
            serial = int(str(row[0]).rsplit("-", 1)[1]) + 1
        except Exception:
            serial = 1
    return f"{value_prefix}{serial:06d}"


def simplified_tables() -> set[str]:
    return {
        "service_order_interests",
        "remote_supplier_batches",
        "remote_supplier_batch_links",
        "remote_order_issued_assets",
    }


def ensure_simplified_service_schema(conn: sqlite3.Connection | None = None) -> list[str]:
    """Create only additive tables/indexes and install the new remote profile."""
    owns = conn is None
    conn = conn or get_conn()
    changes: list[str] = []
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS service_order_interests (
                id INTEGER PRIMARY KEY,
                interest_number TEXT NOT NULL UNIQUE,
                resident_account_id INTEGER,
                telegram_user_id TEXT,
                apartment_id INTEGER,
                apartment_number TEXT NOT NULL,
                service_code TEXT,
                service_item_code TEXT NOT NULL,
                service_name_snapshot TEXT NOT NULL,
                workflow_profile_code TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                unit_price_snapshot REAL NOT NULL,
                amount_due_snapshot REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'UAH',
                interest_status TEXT NOT NULL DEFAULT 'INTEREST',
                payment_notice_id INTEGER,
                payment_notice_number TEXT,
                payment_id INTEGER,
                service_order_id INTEGER,
                resident_comment TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                paid_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_service_interest_resident
            ON service_order_interests(resident_account_id, id)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_service_interest_payment_notice
            ON service_order_interests(payment_notice_id)
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_service_interest_payment
            ON service_order_interests(payment_id)
            WHERE payment_id IS NOT NULL
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS remote_supplier_batches (
                id INTEGER PRIMARY KEY,
                batch_number TEXT NOT NULL UNIQUE,
                service_item_code TEXT NOT NULL,
                service_name_snapshot TEXT NOT NULL,
                quantity_requested INTEGER NOT NULL CHECK(quantity_requested > 0),
                quantity_received INTEGER NOT NULL DEFAULT 0 CHECK(quantity_received >= 0),
                quantity_issued INTEGER NOT NULL DEFAULT 0 CHECK(quantity_issued >= 0),
                batch_status TEXT NOT NULL DEFAULT 'DRAFT',
                supplier_name TEXT,
                supplier_reference TEXT,
                created_by TEXT,
                ordered_at TEXT,
                received_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                note TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_remote_supplier_batches_status
            ON remote_supplier_batches(batch_status, service_item_code, id)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS remote_supplier_batch_links (
                id INTEGER PRIMARY KEY,
                supplier_batch_id INTEGER NOT NULL,
                service_order_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                issued_quantity INTEGER NOT NULL DEFAULT 0 CHECK(issued_quantity >= 0),
                link_status TEXT NOT NULL DEFAULT 'PLANNED',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(supplier_batch_id, service_order_id)
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_supplier_batch_links_order
            ON remote_supplier_batch_links(service_order_id, link_status)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS remote_order_issued_assets (
                id INTEGER PRIMARY KEY,
                service_order_id INTEGER NOT NULL,
                remote_asset_id INTEGER NOT NULL,
                supplier_batch_id INTEGER NOT NULL,
                issued_at TEXT NOT NULL,
                issued_by TEXT,
                note TEXT,
                UNIQUE(service_order_id, remote_asset_id)
            )
            """
        )

        changes.extend(_ensure_new_remote_preorder_profile(cur))
        if owns:
            conn.commit()
        return changes
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def _insert_dynamic(cur: sqlite3.Cursor, table: str, values: dict[str, Any]) -> None:
    cols = table_columns(cur, table)
    selected = [name for name in values if name in cols]
    if not selected:
        raise RuntimeError(f"Не удалось определить поля для вставки в {table}.")
    cur.execute(
        f"INSERT INTO {_quote(table)} ({', '.join(_quote(name) for name in selected)}) "
        f"VALUES ({', '.join('?' for _ in selected)})",
        tuple(values[name] for name in selected),
    )


def _ensure_new_remote_preorder_profile(cur: sqlite3.Cursor) -> list[str]:
    changes: list[str] = []
    required = {"service_workflow_profiles", "service_workflow_steps", "service_item_workflows"}
    missing = [name for name in required if not table_exists(cur, name)]
    if missing:
        raise RuntimeError("Не завершена базовая миграция услуг: " + ", ".join(missing))

    profile = _dict(
        cur,
        "SELECT * FROM service_workflow_profiles WHERE profile_code = ?",
        (NEW_REMOTE_PROFILE,),
    )
    if not profile:
        old = _dict(
            cur,
            "SELECT * FROM service_workflow_profiles WHERE profile_code = ?",
            (NEW_REMOTE_OLD_PROFILE,),
        )
        values = dict(old or {})
        values.pop("id", None)
        values.update(
            {
                "profile_code": NEW_REMOTE_PROFILE,
                "profile_name": "Новий пульт — оплачений передзамовлення",
                "description": (
                    "Оплачений передзаказ. До поставки нет индивидуального складского "
                    "резерва; заявки группируются в заказ поставщику."
                ),
                "service_category": "REMOTE",
                "is_active": 1,
            }
        )
        _insert_dynamic(cur, "service_workflow_profiles", values)
        changes.append("created REMOTE_NEW_PREORDER workflow profile")

    cur.execute(
        "SELECT COUNT(*) FROM service_workflow_steps WHERE profile_code = ?",
        (NEW_REMOTE_PROFILE,),
    )
    if int(cur.fetchone()[0] or 0) == 0:
        old_step = _dict(
            cur,
            "SELECT * FROM service_workflow_steps WHERE profile_code = ? ORDER BY sequence_no, id LIMIT 1",
            (NEW_REMOTE_OLD_PROFILE,),
        ) or {}
        old_step.pop("id", None)
        steps = [
            ("PAYMENT_CONFIRMED", "Оплата підтверджена", "PAYMENT", 10),
            ("SUPPLIER_BATCH_ASSIGNED", "Включено до замовлення постачальнику", "SUPPLY", 20),
            ("SUPPLIER_BATCH_RECEIVED", "Поставка отримана", "SUPPLY", 30),
            ("REMOTE_BATCH_ISSUED", "Пульти видано мешканцю", "ISSUE", 40),
        ]
        for code, name, kind, sequence in steps:
            values = dict(old_step)
            values.update(
                {
                    "profile_code": NEW_REMOTE_PROFILE,
                    "step_code": code,
                    "step_name": name,
                    "step_kind": kind,
                    "sequence_no": sequence,
                    "is_required": 1,
                    "is_active": 1,
                    "created_at": now_db(),
                    "updated_at": now_db(),
                }
            )
            _insert_dynamic(cur, "service_workflow_steps", values)
        changes.append("created REMOTE_NEW_PREORDER workflow steps")

    cur.execute(
        """
        UPDATE service_item_workflows
        SET workflow_profile_code = ?
        WHERE workflow_profile_code = ?
          AND COALESCE(is_active, 1) = 1
        """,
        (NEW_REMOTE_PROFILE, NEW_REMOTE_OLD_PROFILE),
    )
    if cur.rowcount:
        changes.append(f"moved {cur.rowcount} active new-remote service item(s) to paid preorder workflow")
    return changes


def _service_item_snapshot(cur: sqlite3.Cursor, service_item_code: str) -> dict:
    if not table_exists(cur, "service_items"):
        raise RuntimeError("Не найдена таблица service_items.")
    cols = table_columns(cur, "service_items")
    if "service_item_code" not in cols:
        raise RuntimeError("В service_items отсутствует service_item_code.")
    fields = [
        "service_item_code",
        (
            "service_code"
            if "service_code" in cols
            else (
                "base_service_code AS service_code"
                if "base_service_code" in cols
                else "NULL AS service_code"
            )
        ),
        "service_item_name" if "service_item_name" in cols else "service_item_code AS service_item_name",
        "currency" if "currency" in cols else "'UAH' AS currency",
        "is_active" if "is_active" in cols else "1 AS is_active",
        "status" if "status" in cols else "'active' AS status",
    ]
    item = _dict(
        cur,
        f"SELECT {', '.join(fields)} FROM service_items WHERE service_item_code = ?",
        (service_item_code,),
    )
    if not item:
        raise ValueError(f"Статья услуги не найдена: {service_item_code}")
    if int(item.get("is_active") or 0) != 1 or text(item.get("status")).lower() not in {"", "active"}:
        raise ValueError("Эта услуга сейчас не активна.")
    return item


def create_service_interest(
    *,
    resident_account_id: int | None,
    telegram_user_id: int | str | None,
    apartment_id: int | None,
    apartment_number: str,
    service_item_code: str,
    quantity: int,
    resident_comment: str = "",
    service_name_snapshot_override: str = "",
    unit_price_snapshot_override: float | None = None,
    amount_due_snapshot_override: float | None = None,
    currency_snapshot_override: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Record non-binding demand. It becomes an operational order only after payment."""
    quantity = int(quantity)
    if quantity <= 0:
        raise ValueError("Количество должно быть больше нуля.")
    owns = conn is None
    conn = conn or get_conn()
    try:
        ensure_simplified_service_schema(conn)
        cur = conn.cursor()
        item = _service_item_snapshot(cur, service_item_code)
        workflow = get_service_workflow(cur, service_item_code)
        if int(workflow.get("resident_request_enabled") or 0) != 1:
            raise PermissionError("Эта услуга пока недоступна для самостоятельной заявки.")
        catalog_price, catalog_currency = effective_price(cur, service_item_code)
        override_used = any(
            value is not None and value != ""
            for value in (
                unit_price_snapshot_override,
                amount_due_snapshot_override,
                currency_snapshot_override,
                service_name_snapshot_override,
            )
        )
        if override_used:
            if unit_price_snapshot_override is None or amount_due_snapshot_override is None:
                raise ValueError(
                    "Для фиксированной цены намерения нужны цена единицы и итоговая сумма."
                )
            price = round(float(unit_price_snapshot_override), 2)
            amount_due = round(float(amount_due_snapshot_override), 2)
            if price < 0 or amount_due < 0:
                raise ValueError("Фиксированная цена намерения не может быть отрицательной.")
            expected = round(price * quantity, 2)
            if abs(expected - amount_due) > 0.01:
                raise ValueError(
                    "Итоговая фиксированная сумма не соответствует цене единицы и количеству."
                )
            currency = text(currency_snapshot_override) or text(catalog_currency) or "UAH"
            service_name_snapshot = (
                text(service_name_snapshot_override)
                or text(item.get("service_item_name"))
                or service_item_code
            )
        else:
            price, currency = catalog_price, catalog_currency
            if price is None or float(price) <= 0:
                raise ValueError("Для услуги не задана положительная цена.")
            amount_due = round(float(price) * quantity, 2)
            service_name_snapshot = text(item.get("service_item_name")) or service_item_code
        interest_number = _number("SI", cur, "service_order_interests", "interest_number")
        cur.execute(
            """
            INSERT INTO service_order_interests (
                interest_number, resident_account_id, telegram_user_id,
                apartment_id, apartment_number,
                service_code, service_item_code, service_name_snapshot,
                workflow_profile_code, quantity, unit_price_snapshot,
                amount_due_snapshot, currency, interest_status,
                resident_comment, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                interest_number,
                resident_account_id,
                str(telegram_user_id) if telegram_user_id is not None else None,
                apartment_id,
                text(apartment_number),
                text(item.get("service_code")) or None,
                service_item_code,
                service_name_snapshot,
                workflow["workflow_profile_code"],
                quantity,
                float(price),
                amount_due,
                currency or "UAH",
                INTEREST,
                text(resident_comment) or None,
                now_db(),
                now_db(),
            ),
        )
        result = get_service_interest(int(cur.lastrowid), conn=conn)
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


def get_service_interest(interest_id: int, *, conn: sqlite3.Connection | None = None) -> dict:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        row = _dict(cur, "SELECT * FROM service_order_interests WHERE id = ?", (int(interest_id),))
        if not row:
            raise ValueError(f"Намерение не найдено: {interest_id}")
        return row
    finally:
        if owns:
            conn.close()


def list_resident_service_interests(resident_account_id: int, *, conn: sqlite3.Connection | None = None) -> list[dict]:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        return _rows(
            cur,
            """
            SELECT *
            FROM service_order_interests
            WHERE resident_account_id = ?
            ORDER BY id DESC
            LIMIT 80
            """,
            (int(resident_account_id),),
        )
    finally:
        if owns:
            conn.close()


def _notice_id(conn: sqlite3.Connection, notice: dict) -> tuple[int | None, str]:
    number = text(notice.get("notice_number"))
    direct = notice.get("id") or notice.get("payment_notice_id")
    if direct is not None:
        try:
            return int(direct), number
        except (TypeError, ValueError):
            pass
    cur = conn.cursor()
    if not table_exists(cur, "payment_notices"):
        return None, number
    cols = table_columns(cur, "payment_notices")
    if not number or "id" not in cols or "notice_number" not in cols:
        return None, number
    row = _dict(cur, "SELECT id FROM payment_notices WHERE notice_number = ?", (number,))
    return (int(row["id"]) if row else None), number


def attach_payment_notice_to_interest(
    interest_id: int,
    notice: dict,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        interest = get_service_interest(interest_id, conn=conn)
        if text(interest.get("interest_status")) in {PAID_ORDER_CREATED, CANCELLED}:
            raise ValueError("Эта запись уже не ожидает оплаты.")
        notice_id, notice_number = _notice_id(conn, notice)
        if notice_id is None:
            raise RuntimeError(
                "Не удалось определить ID платёжного уведомления. "
                "Уведомление не привязано к намерению."
            )
        cur.execute(
            """
            UPDATE service_order_interests
            SET payment_notice_id = ?,
                payment_notice_number = ?,
                interest_status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (notice_id, notice_number or None, PAYMENT_NOTICE, now_db(), int(interest_id)),
        )
        result = get_service_interest(interest_id, conn=conn)
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


def _payment_candidates(cur: sqlite3.Cursor, interest: dict) -> list[dict]:
    if not table_exists(cur, "payments"):
        return []
    cols = table_columns(cur, "payments")
    required = {"id", "amount"}
    if not required.issubset(cols):
        return []
    fields = [
        "id", "amount",
        "apartment_id" if "apartment_id" in cols else "NULL AS apartment_id",
        "apartment_number" if "apartment_number" in cols else "NULL AS apartment_number",
        "service_item_code" if "service_item_code" in cols else "NULL AS service_item_code",
        "payment_notice_id" if "payment_notice_id" in cols else "NULL AS payment_notice_id",
        "currency" if "currency" in cols else "'UAH' AS currency",
    ]
    where = ["COALESCE(amount, 0) > 0"]
    params: list[Any] = []
    if "cashier_entry_status" in cols:
        where.append("COALESCE(cashier_entry_status, 'CONFIRMED') = 'CONFIRMED'")
    if interest.get("payment_notice_id") is not None and "payment_notice_id" in cols:
        where.append("payment_notice_id = ?")
        params.append(int(interest["payment_notice_id"]))
    else:
        # A notice ID is the normal path. This guarded fallback is only for
        # legacy payments and is intentionally strict: no silent ambiguous link.
        if interest.get("apartment_id") is not None and "apartment_id" in cols:
            where.append("apartment_id = ?")
            params.append(int(interest["apartment_id"]))
        elif "apartment_number" in cols:
            where.append("TRIM(CAST(apartment_number AS TEXT)) = ?")
            params.append(text(interest.get("apartment_number")))
        else:
            return []
        if "service_item_code" in cols:
            where.append("COALESCE(service_item_code, '') = COALESCE(?, '')")
            params.append(interest.get("service_item_code"))
        else:
            return []
        where.append("amount + 0.00001 >= ?")
        params.append(float(interest.get("amount_due_snapshot") or 0))

    if table_exists(cur, "service_order_payment_links"):
        where.append(
            "NOT EXISTS (SELECT 1 FROM service_order_payment_links l WHERE l.payment_id = payments.id)"
        )
    cur.execute(
        f"SELECT {', '.join(fields)} FROM payments WHERE {' AND '.join(where)} ORDER BY id ASC LIMIT 3",
        tuple(params),
    )
    return [dict(row) for row in cur.fetchall()]


def reconcile_paid_service_interests(
    *,
    limit: int = 100,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    """Promote unambiguous confirmed paid interests to real service orders."""
    owns = conn is None
    conn = conn or get_conn()
    created: list[dict] = []
    try:
        ensure_simplified_service_schema(conn)
        cur = conn.cursor()
        interests = _rows(
            cur,
            """
            SELECT * FROM service_order_interests
            WHERE interest_status = ?
              AND payment_id IS NULL
              AND service_order_id IS NULL
            ORDER BY id ASC
            LIMIT ?
            """,
            (PAYMENT_NOTICE, int(limit)),
        )
        for interest in interests:
            candidates = _payment_candidates(cur, interest)
            if len(candidates) != 1:
                continue
            payment = candidates[0]
            due = float(interest.get("amount_due_snapshot") or 0)
            if due <= 0 or float(payment.get("amount") or 0) + 0.00001 < due:
                continue
            order = create_service_order(
                resident_account_id=interest.get("resident_account_id"),
                telegram_user_id=interest.get("telegram_user_id"),
                apartment_id=interest.get("apartment_id"),
                apartment_number=text(interest.get("apartment_number")),
                service_item_code=text(interest.get("service_item_code")),
                quantity=float(interest.get("quantity") or 1),
                resident_comment=text(interest.get("resident_comment")),
                service_name_snapshot_override=text(interest.get("service_name_snapshot")),
                unit_price_snapshot_override=float(interest.get("unit_price_snapshot") or 0),
                amount_due_snapshot_override=float(interest.get("amount_due_snapshot") or 0),
                currency_snapshot_override=text(interest.get("currency")) or "UAH",
                actor_id=None,
                source_context=f"paid_interest:{interest['interest_number']}",
                conn=conn,
            )
            linked = link_payment_to_order(
                order_id=int(order["id"]),
                payment_id=int(payment["id"]),
                amount=due,
                actor_id=None,
                note=(
                    f"Автоматически создано из оплаченного намерения "
                    f"{interest['interest_number']}."
                ),
                conn=conn,
            )
            # A two-barrier phone request has its own normalized request record.
            # The import is deliberately lazy so ordinary remote flows do not
            # depend on the phone-access module.
            from phone_barrier_access_service import promote_paid_phone_barrier_access_interest
            promote_paid_phone_barrier_access_interest(
                interest=interest,
                order=linked["order"],
                payment_id=int(payment["id"]),
                conn=conn,
            )

            cur.execute(
                """
                UPDATE service_order_interests
                SET interest_status = ?, payment_id = ?, service_order_id = ?,
                    paid_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    PAID_ORDER_CREATED,
                    int(payment["id"]),
                    int(order["id"]),
                    now_db(),
                    now_db(),
                    int(interest["id"]),
                ),
            )
            created.append(
                {
                    "interest_id": int(interest["id"]),
                    "interest_number": interest["interest_number"],
                    "payment_id": int(payment["id"]),
                    "order": linked["order"],
                }
            )
        if owns:
            conn.commit()
        return created
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def unpaid_interest_totals(*, conn: sqlite3.Connection | None = None) -> list[dict]:
    """For statistics only: non-paid interest remains visible but not operational."""
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        return _rows(
            cur,
            """
            SELECT service_item_code, service_name_snapshot,
                   SUM(quantity) AS requested_quantity,
                   COUNT(*) AS interest_count
            FROM service_order_interests
            WHERE interest_status IN (?, ?)
            GROUP BY service_item_code, service_name_snapshot
            ORDER BY service_name_snapshot, service_item_code
            """,
            (INTEREST, PAYMENT_NOTICE),
        )
    finally:
        if owns:
            conn.close()


def supplier_demand(*, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Return only paid, real orders not yet put into a supplier batch."""
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        return _rows(
            cur,
            """
            SELECT o.service_item_code,
                   o.service_name_snapshot,
                   SUM(CAST(o.quantity AS INTEGER)) AS quantity,
                   COUNT(*) AS order_count
            FROM service_orders o
            JOIN service_order_steps p
              ON p.service_order_id = o.id
             AND p.step_code = 'PAYMENT_CONFIRMED'
             AND p.step_status IN ('CONFIRMED', 'WAIVED')
            JOIN service_order_steps s
              ON s.service_order_id = o.id
             AND s.step_code = 'SUPPLIER_BATCH_ASSIGNED'
             AND s.step_status = 'WAITING'
            WHERE o.workflow_profile_code = ?
              AND o.order_status NOT IN ('COMPLETED', 'CANCELLED')
              AND NOT EXISTS (
                  SELECT 1 FROM remote_supplier_batch_links l
                  WHERE l.service_order_id = o.id
              )
            GROUP BY o.service_item_code, o.service_name_snapshot
            ORDER BY o.service_name_snapshot, o.service_item_code
            """,
            (NEW_REMOTE_PROFILE,),
        )
    finally:
        if owns:
            conn.close()


def create_supplier_batch(
    *,
    service_item_code: str,
    actor_id: int | str | None,
    supplier_name: str = "",
    note: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Aggregate all currently paid, unbatched orders of one remote type."""
    owns = conn is None
    conn = conn or get_conn()
    try:
        ensure_simplified_service_schema(conn)
        cur = conn.cursor()
        orders = _rows(
            cur,
            """
            SELECT o.*
            FROM service_orders o
            JOIN service_order_steps p
              ON p.service_order_id = o.id
             AND p.step_code = 'PAYMENT_CONFIRMED'
             AND p.step_status IN ('CONFIRMED', 'WAIVED')
            JOIN service_order_steps s
              ON s.service_order_id = o.id
             AND s.step_code = 'SUPPLIER_BATCH_ASSIGNED'
             AND s.step_status = 'WAITING'
            WHERE o.workflow_profile_code = ?
              AND o.service_item_code = ?
              AND o.order_status NOT IN ('COMPLETED', 'CANCELLED')
              AND NOT EXISTS (
                  SELECT 1 FROM remote_supplier_batch_links l
                  WHERE l.service_order_id = o.id
              )
            ORDER BY o.id
            """,
            (NEW_REMOTE_PROFILE, service_item_code),
        )
        if not orders:
            raise ValueError("Нет оплаченных передзаказов этого типа для заказа поставщику.")
        requested = sum(int(float(row.get("quantity") or 0)) for row in orders)
        if requested <= 0:
            raise ValueError("Некорректное суммарное количество передзаказов.")
        batch_number = _number("RB", cur, "remote_supplier_batches", "batch_number")
        cur.execute(
            """
            INSERT INTO remote_supplier_batches (
                batch_number, service_item_code, service_name_snapshot,
                quantity_requested, quantity_received, quantity_issued,
                batch_status, supplier_name, created_by, ordered_at,
                created_at, updated_at, note
            )
            VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_number,
                service_item_code,
                text(orders[0].get("service_name_snapshot")) or service_item_code,
                requested,
                BATCH_ORDERED,
                text(supplier_name) or None,
                str(actor_id) if actor_id is not None else "system",
                now_db(), now_db(), now_db(), text(note) or None,
            ),
        )
        batch_id = int(cur.lastrowid)
        for order in orders:
            quantity = int(float(order.get("quantity") or 0))
            cur.execute(
                """
                INSERT INTO remote_supplier_batch_links (
                    supplier_batch_id, service_order_id, quantity,
                    issued_quantity, link_status, created_at, updated_at
                ) VALUES (?, ?, ?, 0, ?, ?, ?)
                """,
                (batch_id, int(order["id"]), quantity, LINK_PLANNED, now_db(), now_db()),
            )
            confirm_order_step(
                order_id=int(order["id"]),
                step_code="SUPPLIER_BATCH_ASSIGNED",
                actor_id=actor_id,
                note=f"Включено в заказ поставщику {batch_number}: {quantity} шт.",
                source_context="supplier_batch",
                conn=conn,
            )
        result = get_supplier_batch(batch_id, conn=conn)
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


def list_supplier_batches(*, conn: sqlite3.Connection | None = None) -> list[dict]:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        return _rows(
            cur,
            """
            SELECT * FROM remote_supplier_batches
            WHERE batch_status NOT IN (?, ?)
            ORDER BY id DESC
            LIMIT 80
            """,
            (BATCH_CLOSED, BATCH_DRAFT),
        )
    finally:
        if owns:
            conn.close()


def get_supplier_batch(batch_id: int, *, conn: sqlite3.Connection | None = None) -> dict:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        batch = _dict(cur, "SELECT * FROM remote_supplier_batches WHERE id = ?", (int(batch_id),))
        if not batch:
            raise ValueError("Заказ поставщику не найден.")
        batch["links"] = _rows(
            cur,
            """
            SELECT l.*, o.order_number, o.apartment_number, o.quantity,
                   o.service_name_snapshot, o.order_status
            FROM remote_supplier_batch_links l
            JOIN service_orders o ON o.id = l.service_order_id
            WHERE l.supplier_batch_id = ?
            ORDER BY l.id
            """,
            (int(batch_id),),
        )
        return batch
    finally:
        if owns:
            conn.close()


def receive_supplier_batch(
    *,
    batch_id: int,
    received_now: int,
    actor_id: int | str | None,
    note: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Receive a delivery and make fully covered resident orders ready for issue."""
    received_now = int(received_now)
    if received_now <= 0:
        raise ValueError("Количество полученных пультов должно быть больше нуля.")
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        batch = get_supplier_batch(batch_id, conn=conn)
        requested = int(batch.get("quantity_requested") or 0)
        received = int(batch.get("quantity_received") or 0)
        issued = int(batch.get("quantity_issued") or 0)
        if received + received_now > requested:
            raise ValueError("Нельзя принять больше пультов, чем заказано в этой партии.")
        available_before = received - issued
        available_after = available_before + received_now
        cur.execute(
            """
            UPDATE remote_supplier_batches
            SET quantity_received = ?,
                batch_status = ?,
                received_at = ?,
                updated_at = ?,
                note = COALESCE(?, note)
            WHERE id = ?
            """,
            (
                received + received_now,
                BATCH_RECEIVED if received + received_now >= requested else BATCH_PARTIAL,
                now_db(), now_db(), text(note) or None, int(batch_id),
            ),
        )
        # The queue is FIFO. An order becomes ready only when the batch can
        # cover its full requested quantity; no fractional resident issue.
        links = _rows(
            cur,
            """
            SELECT * FROM remote_supplier_batch_links
            WHERE supplier_batch_id = ? AND link_status = ?
            ORDER BY id
            """,
            (int(batch_id), LINK_PLANNED),
        )
        capacity = available_after
        for link in links:
            qty = int(link.get("quantity") or 0)
            if qty <= 0 or capacity < qty:
                continue
            cur.execute(
                """
                UPDATE remote_supplier_batch_links
                SET link_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (LINK_READY, now_db(), int(link["id"])),
            )
            confirm_order_step(
                order_id=int(link["service_order_id"]),
                step_code="SUPPLIER_BATCH_RECEIVED",
                actor_id=actor_id,
                note=f"Поставка {batch['batch_number']} получена; доступно {qty} шт. для выдачи.",
                source_context="supplier_batch",
                conn=conn,
            )
            capacity -= qty
        result = get_supplier_batch(batch_id, conn=conn)
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


def order_supply_link(service_order_id: int, *, conn: sqlite3.Connection | None = None) -> dict | None:
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        return _dict(
            cur,
            """
            SELECT l.*, b.batch_number, b.batch_status,
                   b.quantity_requested, b.quantity_received, b.quantity_issued,
                   b.service_item_code
            FROM remote_supplier_batch_links l
            JOIN remote_supplier_batches b ON b.id = l.supplier_batch_id
            WHERE l.service_order_id = ?
            ORDER BY l.id DESC
            LIMIT 1
            """,
            (int(service_order_id),),
        )
    finally:
        if owns:
            conn.close()


def issue_new_remotes_from_batch(
    *,
    service_order_id: int,
    actor_id: int | str | None,
    note: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Create actual asset records at issue time and complete the paid preorder."""
    owns = conn is None
    conn = conn or get_conn()
    try:
        cur = conn.cursor()
        order = get_service_order(cur, service_order_id)
        if text(order.get("workflow_profile_code")) != NEW_REMOTE_PROFILE:
            raise ValueError("Этот заказ не является предзаказом нового пульта.")
        link = order_supply_link(service_order_id, conn=conn)
        if not link or text(link.get("link_status")) != LINK_READY:
            raise ValueError("Для заявки ещё нет полученной партии, готовой к выдаче.")
        quantity = int(link.get("quantity") or 0)
        if quantity <= 0:
            raise ValueError("В привязке к поставке отсутствует количество.")
        batch = get_supplier_batch(int(link["supplier_batch_id"]), conn=conn)
        available = int(batch.get("quantity_received") or 0) - int(batch.get("quantity_issued") or 0)
        if available < quantity:
            raise ValueError("В поставке недостаточно фактически полученных пультов.")
        issued_assets: list[int] = []
        start = int(batch.get("quantity_issued") or 0) + 1
        for position in range(quantity):
            asset_number = f"{batch['batch_number']}-{start + position:03d}"
            asset = create_remote_asset(
                asset_number=asset_number,
                ownership_type="OSBB_STOCK",
                inventory_status="RECEIVED",
                condition_status="NEW",
                apartment_id=int(order["apartment_id"]) if order.get("apartment_id") is not None else None,
                apartment_number=text(order.get("apartment_number")),
                actor_id=actor_id,
                note=f"Получен в поставке {batch['batch_number']} для выдачи по {order['order_number']}.",
                conn=conn,
            )
            asset_id = int(asset["id"])
            record_remote_movement(
                remote_asset_id=asset_id,
                service_order_id=int(service_order_id),
                movement_type="ISSUED_FROM_SUPPLIER_BATCH",
                to_state="WITH_RESIDENT",
                actor_id=actor_id,
                apartment_id=int(order["apartment_id"]) if order.get("apartment_id") is not None else None,
                apartment_number=text(order.get("apartment_number")),
                note=f"Выдан по оплаченной заявке из поставки {batch['batch_number']}.",
                confirm_step_code="REMOTE_BATCH_ISSUED",
                conn=conn,
            )
            cur.execute(
                """
                UPDATE remote_assets
                SET ownership_type = 'RESIDENT', issued_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now_db(), now_db(), asset_id),
            )
            cur.execute(
                """
                INSERT INTO remote_order_issued_assets (
                    service_order_id, remote_asset_id, supplier_batch_id,
                    issued_at, issued_by, note
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(service_order_id), asset_id, int(batch["id"]), now_db(),
                    str(actor_id) if actor_id is not None else "system", text(note) or None,
                ),
            )
            issued_assets.append(asset_id)
        cur.execute(
            """
            UPDATE remote_supplier_batch_links
            SET issued_quantity = ?, link_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (quantity, LINK_ISSUED, now_db(), int(link["id"])),
        )
        issued_total = int(batch.get("quantity_issued") or 0) + quantity
        received_total = int(batch.get("quantity_received") or 0)
        cur.execute(
            """
            UPDATE remote_supplier_batches
            SET quantity_issued = ?,
                batch_status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                issued_total,
                BATCH_CLOSED if issued_total >= received_total and received_total >= int(batch.get("quantity_requested") or 0) else BATCH_RECEIVED,
                now_db(),
                int(batch["id"]),
            ),
        )
        result = get_service_order(cur, int(service_order_id))
        if owns:
            conn.commit()
        return {"order": result, "asset_ids": issued_assets, "batch": get_supplier_batch(int(batch["id"]), conn=conn)}
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()
