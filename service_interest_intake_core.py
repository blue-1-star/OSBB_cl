"""Operator entry of a resident's message without requiring a bot account.

An assertion such as "I handed money to the concierge" is preserved as source
evidence, never as a confirmed payment. Cash is recorded only by the cashier.
"""

from __future__ import annotations

from datetime import date
import sqlite3

from audit_logger import audit_log
from cash_claim_points_core import list_claim_points
from service_orders_core import get_conn
from service_preorders_core import create_service_interest, now_db


CHANNELS = {"TELEGRAM", "VIBER", "OTHER_MESSENGER", "PHONE", "PAPER", "OTHER"}


def ensure_intake_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS service_interest_intake (
            id INTEGER PRIMARY KEY,
            interest_id INTEGER NOT NULL UNIQUE,
            source_channel TEXT NOT NULL,
            original_message TEXT NOT NULL,
            sender_label TEXT,
            source_reference TEXT,
            message_received_at TEXT NOT NULL,
            entered_by TEXT NOT NULL,
            claimed_cash_handover INTEGER NOT NULL DEFAULT 0,
            claimed_cashbox TEXT,
            verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
            created_at TEXT NOT NULL,
            FOREIGN KEY(interest_id) REFERENCES service_order_interests(id)
        )"""
    )


def record_external_interest(
    *, apartment_number: str, quantity: int, original_message: str,
    source_channel: str, entered_by: str, service_item_code: str = "REMOTE_NEW",
    sender_label: str = "", source_reference: str = "",
    message_received_at: str = "", claimed_cash_handover: bool = False,
    claimed_cashbox: str = "", allow_duplicate: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Create an unverified interest and provenance record in one transaction."""
    apartment_number = str(apartment_number).strip()
    original_message = original_message.strip()
    source_channel = source_channel.strip().upper()
    entered_by = entered_by.strip()
    if not apartment_number or not original_message or not entered_by:
        raise ValueError("Нужны квартира, исходное сообщение и имя внесшего оператора.")
    if source_channel not in CHANNELS:
        raise ValueError("Неизвестный канал получения сообщения.")
    if int(quantity) < 1:
        raise ValueError("Количество должно быть положительным.")
    received_day = date.fromisoformat((message_received_at or date.today().isoformat())[:10])
    if received_day > date.today():
        raise ValueError("Дата исходного сообщения не может быть в будущем.")
    claimed_cashbox = claimed_cashbox.strip().upper()
    if claimed_cash_handover and not claimed_cashbox:
        raise ValueError("Выберите заранее обозначенный пункт передачи денег.")
    if not claimed_cash_handover and claimed_cashbox:
        raise ValueError("Пункт передачи указан без заявления жителя о передаче денег.")
    owns = conn is None
    conn = conn or get_conn()
    try:
        ensure_intake_schema(conn)
        if claimed_cash_handover:
            eligible = {p["point_code"] for p in list_claim_points(received_day.isoformat(), conn=conn)}
            if claimed_cashbox not in eligible:
                raise ValueError(
                    "Пункт приёма не найден среди действующих K1–K6/O или у ячейки И нет "
                    "уполномоченного на дату сообщения."
                )
        units = conn.execute(
            """SELECT id, apartment_number FROM apartments
               WHERE TRIM(CAST(apartment_number AS TEXT))=?
                 AND COALESCE(unit_type,'')<>'TECHNICAL'
                 AND COALESCE(record_status,'')<>'TEST'""",
            (apartment_number,),
        ).fetchall()
        if len(units) != 1:
            raise ValueError("Квартира не найдена однозначно в жилом реестре; требуется ручная проверка адреса.")
        if not allow_duplicate:
            existing = conn.execute(
                """SELECT interest_number FROM service_order_interests
                   WHERE apartment_id=? AND service_item_code=?
                     AND interest_status IN ('INTEREST','PAYMENT_NOTICE')
                   ORDER BY id DESC LIMIT 1""",
                (int(units[0]["id"]), service_item_code),
            ).fetchone()
            if existing:
                raise ValueError(
                    f"По квартире уже открыто намерение {existing[0]}. "
                    "Проверьте, не повторное ли это сообщение."
                )
        interest = create_service_interest(
            resident_account_id=None, telegram_user_id=None,
            apartment_id=int(units[0]["id"]), apartment_number=apartment_number,
            service_item_code=service_item_code, quantity=int(quantity),
            resident_comment="Внесено оператором из сообщения; источник и исходный текст — в service_interest_intake.",
            conn=conn,
        )
        conn.execute(
            """INSERT INTO service_interest_intake
               (interest_id,source_channel,original_message,sender_label,source_reference,
                message_received_at,entered_by,claimed_cash_handover,claimed_cashbox,
                verification_status,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,'UNVERIFIED',?)""",
            (int(interest["id"]), source_channel, original_message,
             sender_label.strip() or None, source_reference.strip() or None,
             message_received_at.strip() or now_db(), entered_by,
             int(claimed_cash_handover), claimed_cashbox or None, now_db()),
        )
        audit_log(
            conn=conn, operator_id=entered_by, user_id=entered_by,
            actor_type="operator", action_type="external_service_interest_created",
            table_name="service_order_interests", row_id=int(interest["id"]),
            field_name="interest_status", old_value="", new_value="INTEREST",
            source_context="service_interest_intake_core",
            comment=f"Канал {source_channel}; сообщение сохранено отдельно; заявление об оплате не подтверждено.",
            commit=False,
        )
        if owns:
            conn.commit()
        return interest
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()
