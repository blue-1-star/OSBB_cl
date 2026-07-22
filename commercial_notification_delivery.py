"""
Доставка подготовленных Telegram-уведомлений по договорам КП.

Модуль отправляет только записи commercial_notifications со статусом READY.
Он:
- не формирует долг и не решает, кого отключать;
- не отправляет SMS;
- не отправляет GSM-команды GEOS/RC-4000;
- после успешной отправки ставит статус SENT;
- при ошибке ставит FAILED и сохраняет текст ошибки.

Пример будущего вызова из бота/планировщика:
    from commercial_notification_delivery import deliver_ready_notifications
    result = await deliver_ready_notifications(context.bot, limit=20)

Пока планировщик в parking_bot.py не подключается автоматически.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import asyncio
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
for folder in (ROOT, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB
from audit_logger import audit_log


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def get_conn(db_path: str | Path | None = None) -> sqlite3.Connection:
    db = Path(db_path) if db_path else get_db_file()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def ready_notifications(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
) -> list[dict]:
    cur = conn.cursor()
    cur.execute("""
        SELECT
            n.id,
            n.contract_id,
            n.recipient_id,
            n.telegram_user_id,
            n.notification_type,
            n.message_text,
            n.debt_amount_snapshot,
            n.days_overdue_snapshot,
            n.created_at,
            c.contract_number,
            a.unit_code,
            a.apartment_number
        FROM commercial_notifications n
        JOIN commercial_contracts c ON c.id = n.contract_id
        JOIN apartments a ON a.id = c.unit_id
        WHERE n.status = 'READY'
        ORDER BY n.created_at, n.id
        LIMIT ?
    """, (int(limit),))
    return [dict(row) for row in cur.fetchall()]


def mark_sent(
    conn: sqlite3.Connection,
    notification_id: int,
) -> None:
    cur = conn.cursor()
    cur.execute("""
        UPDATE commercial_notifications
        SET
            status = 'SENT',
            sent_at = ?,
            failed_at = NULL,
            delivery_error = NULL
        WHERE id = ?
          AND status = 'READY'
    """, (now_db(), int(notification_id)))


def mark_failed(
    conn: sqlite3.Connection,
    notification_id: int,
    error: str,
) -> None:
    cur = conn.cursor()
    cur.execute("""
        UPDATE commercial_notifications
        SET
            status = 'FAILED',
            failed_at = ?,
            delivery_error = ?
        WHERE id = ?
          AND status = 'READY'
    """, (now_db(), str(error)[:1000], int(notification_id)))


async def deliver_ready_notifications(
    bot,
    *,
    db_path: str | Path | None = None,
    limit: int = 50,
    disable_notification: bool = False,
) -> dict:
    """
    Отправляет Telegram-сообщения из очереди READY.

    bot:
        объект telegram.Bot либо context.bot из python-telegram-bot.

    Возвращает сводку. Ошибка одной записи не останавливает остальные.
    """
    conn = get_conn(db_path)
    result = {
        "selected": 0,
        "sent": 0,
        "failed": 0,
        "skipped_without_telegram": 0,
        "details": [],
    }

    try:
        queue = ready_notifications(conn, limit=limit)
        result["selected"] = len(queue)

        for row in queue:
            notification_id = int(row["id"])
            telegram_user_id = text(row.get("telegram_user_id"))

            if not telegram_user_id:
                mark_failed(
                    conn,
                    notification_id,
                    "Не указан telegram_user_id получателя.",
                )
                audit_log(
                    conn=conn,
                    operator_id="system",
                    user_id="system",
                    actor_type="system",
                    action_type="commercial_telegram_notification_failed",
                    table_name="commercial_notifications",
                    row_id=notification_id,
                    field_name="status",
                    old_value="READY",
                    new_value="FAILED",
                    source_context="commercial_notification_delivery.py",
                    comment="Не указан telegram_user_id получателя.",
                    extra={"contract_id": row["contract_id"]},
                    commit=False,
                )
                result["failed"] += 1
                result["skipped_without_telegram"] += 1
                result["details"].append({
                    "notification_id": notification_id,
                    "result": "FAILED",
                    "error": "telegram_user_id missing",
                })
                continue

            try:
                await bot.send_message(
                    chat_id=int(telegram_user_id),
                    text=row["message_text"],
                    disable_notification=disable_notification,
                )
                mark_sent(conn, notification_id)
                audit_log(
                    conn=conn,
                    operator_id="system",
                    user_id="system",
                    actor_type="system",
                    action_type="commercial_telegram_notification_sent",
                    table_name="commercial_notifications",
                    row_id=notification_id,
                    field_name="status",
                    old_value="READY",
                    new_value="SENT",
                    source_context="commercial_notification_delivery.py",
                    comment="Telegram-уведомление отправлено.",
                    extra={
                        "contract_id": row["contract_id"],
                        "unit_code": row.get("unit_code") or row.get("apartment_number"),
                        "telegram_user_id": telegram_user_id,
                        "notification_type": row["notification_type"],
                    },
                    commit=False,
                )
                result["sent"] += 1
                result["details"].append({
                    "notification_id": notification_id,
                    "result": "SENT",
                })
            except Exception as exc:
                mark_failed(conn, notification_id, str(exc))
                audit_log(
                    conn=conn,
                    operator_id="system",
                    user_id="system",
                    actor_type="system",
                    action_type="commercial_telegram_notification_failed",
                    table_name="commercial_notifications",
                    row_id=notification_id,
                    field_name="status",
                    old_value="READY",
                    new_value="FAILED",
                    source_context="commercial_notification_delivery.py",
                    comment=f"Ошибка Telegram delivery: {exc}",
                    extra={
                        "contract_id": row["contract_id"],
                        "telegram_user_id": telegram_user_id,
                    },
                    commit=False,
                )
                result["failed"] += 1
                result["details"].append({
                    "notification_id": notification_id,
                    "result": "FAILED",
                    "error": str(exc),
                })

        conn.commit()
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    print(
        "Этот модуль предназначен для вызова из Telegram-бота/планировщика.\n"
        "Сначала сформируйте очередь командой:\n"
        "  python commercial_contracts.py --apply\n"
    )
