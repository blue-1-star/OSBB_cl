"""Durable Telegram confirmations for paid remote preorders."""

from __future__ import annotations

import sqlite3

from audit_logger import audit_log
from service_orders_core import get_conn, now_db
from supplier_terms_core import order_preorder_context


def ensure_order_notification_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS service_order_notifications (
            id INTEGER PRIMARY KEY,
            service_order_id INTEGER NOT NULL,
            notification_kind TEXT NOT NULL,
            telegram_user_id TEXT NOT NULL,
            message_text TEXT NOT NULL,
            delivery_status TEXT NOT NULL DEFAULT 'READY',
            created_at TEXT NOT NULL,
            sent_at TEXT,
            delivery_error TEXT,
            UNIQUE(service_order_id, notification_kind)
        )"""
    )


def enqueue_paid_order_confirmation(order_id: int, *,
                                    conn: sqlite3.Connection | None = None) -> dict | None:
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT o.id,o.order_number,o.telegram_user_id,o.apartment_number,
                      o.service_item_code,o.quantity,o.payment_status,
                      COALESCE((SELECT SUM(amount) FROM service_order_payment_links l
                                WHERE l.service_order_id=o.id),0) AS received_amount
               FROM service_orders o WHERE o.id=?""", (int(order_id),),
        ).fetchone()
        if not row or row["service_item_code"] != "REMOTE_NEW" or not row["telegram_user_id"]:
            return None
        amount = float(row["received_amount"])
        if amount <= 0:
            return None
        context = order_preorder_context(int(order_id), conn=conn)
        qty = int(float(row["quantity"]))
        lines = ["✅ Оплату за нові пульти підтверджено", "",
                 f"Квартира {row['apartment_number']} · {qty} шт.",
                 f"Отримано: {amount:.2f} грн."]
        if context["batch_number"]:
            lines.append(f"Партія постачальника: {context['batch_number']}.")
        elif context["minimum"]:
            lines.append(f"Оплачений пакет зараз: {context['quantity']} із мінімальних {context['minimum']} шт.")
            lines.append("Замовлення постачальнику ще не оформлено.")
        else:
            lines.append("Розмір мінімальної партії постачальника ще уточнюється.")
        lines.extend(["", "Деталі: «📦 Мої замовлення» → «📋 Мої послуги»."])
        ensure_order_notification_schema(conn)
        cur = conn.execute(
            """INSERT OR IGNORE INTO service_order_notifications
               (service_order_id,notification_kind,telegram_user_id,message_text,created_at)
               VALUES (?,'PAYMENT_CONFIRMED',?,?,?)""",
            (int(order_id), str(row["telegram_user_id"]), "\n".join(lines), now_db()),
        )
        if cur.rowcount:
            audit_log(
                conn=conn, operator_id="system", user_id=row["telegram_user_id"], actor_type="system",
                action_type="service_order_payment_notification_queued",
                table_name="service_order_notifications", row_id=cur.lastrowid,
                field_name="delivery_status", old_value="", new_value="READY",
                source_context="service_order_notifications", comment=f"Заказ {row['order_number']}",
                commit=False,
            )
        result = conn.execute(
            """SELECT * FROM service_order_notifications
               WHERE service_order_id=? AND notification_kind='PAYMENT_CONFIRMED'""",
            (int(order_id),),
        ).fetchone()
        if owns:
            conn.commit()
        return dict(result) if result else None
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


async def deliver_ready_order_notifications(bot, *, limit: int = 30) -> dict:
    conn = get_conn()
    result = {"selected": 0, "sent": 0, "failed": 0}
    try:
        conn.row_factory = sqlite3.Row
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_order_notifications'"
        ).fetchone():
            return result
        rows = conn.execute(
            """SELECT * FROM service_order_notifications WHERE delivery_status='READY'
               ORDER BY id LIMIT ?""", (int(limit),),
        ).fetchall()
        result["selected"] = len(rows)
        for row in rows:
            try:
                await bot.send_message(chat_id=int(row["telegram_user_id"]), text=row["message_text"])
                conn.execute(
                    """UPDATE service_order_notifications SET delivery_status='SENT',sent_at=?,
                       delivery_error=NULL WHERE id=?""", (now_db(), int(row["id"])),
                )
                result["sent"] += 1
                outcome = "SENT"
            except Exception as exc:
                conn.execute(
                    """UPDATE service_order_notifications SET delivery_status='FAILED',
                       delivery_error=? WHERE id=?""", (str(exc)[:1000], int(row["id"])),
                )
                result["failed"] += 1
                outcome = "FAILED"
            audit_log(
                conn=conn, operator_id="system", user_id=row["telegram_user_id"], actor_type="system",
                action_type="service_order_notification_delivery",
                table_name="service_order_notifications", row_id=row["id"],
                field_name="delivery_status", old_value="READY",
                new_value=outcome,
                source_context="service_order_notifications",
                comment="Отправка уведомления о подтверждённой оплате заказа.", commit=False,
            )
            conn.commit()
        return result
    finally:
        conn.close()
