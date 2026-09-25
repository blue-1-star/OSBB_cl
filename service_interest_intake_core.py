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
from resident_identity_core import resolve_apartment_telegram_account


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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(service_interest_intake)")}
    if "claimed_amount" not in columns:
        conn.execute("ALTER TABLE service_interest_intake ADD COLUMN claimed_amount REAL")


def record_cash_handover_claim(*, interest_id: int, point_code: str, amount: float,
                               actor: str, source_note: str = "",
                               conn: sqlite3.Connection | None = None) -> dict:
    """Record an unverified handover claim for an existing interest; no cash posting."""
    point_code, actor, source_note = point_code.strip().upper(), actor.strip(), source_note.strip()
    amount = round(float(amount), 2)
    if not actor or not point_code or amount <= 0:
        raise ValueError("Нужны исполнитель, пункт передачи и положительная сумма.")
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        if owns:
            conn.execute("BEGIN IMMEDIATE")
        ensure_intake_schema(conn)
        interest = conn.execute(
            "SELECT * FROM service_order_interests WHERE id=?", (int(interest_id),)
        ).fetchone()
        if not interest or interest["interest_status"] != "INTEREST" or interest["payment_id"]:
            raise ValueError("Намерение уже оплачено или не открыто.")
        eligible = {p["point_code"] for p in list_claim_points(conn=conn)} | {"C"}
        if point_code not in eligible:
            raise ValueError("Указанный пункт не действует или сборщик не назначен.")
        existing = conn.execute(
            "SELECT * FROM service_interest_intake WHERE interest_id=?", (int(interest_id),)
        ).fetchone()
        if existing and existing["verification_status"] != "UNVERIFIED":
            raise ValueError("Заявление уже обработано; менять его нельзя.")
        old_value = (f"{existing['claimed_cashbox']},{existing['claimed_amount']}"
                     if existing and int(existing["claimed_cash_handover"] or 0) else "")
        if existing:
            conn.execute(
                """UPDATE service_interest_intake SET claimed_cash_handover=1,
                   claimed_cashbox=?, claimed_amount=?, verification_status='UNVERIFIED'
                   WHERE interest_id=?""", (point_code, amount, int(interest_id)),
            )
        else:
            conn.execute(
                """INSERT INTO service_interest_intake
                   (interest_id,source_channel,original_message,message_received_at,
                    entered_by,claimed_cash_handover,claimed_cashbox,claimed_amount,
                    verification_status,created_at)
                   VALUES (?,'OPERATOR',?,?,?,1,?,?,'UNVERIFIED',?)""",
                (int(interest_id), source_note or "Заявлена передача денег по ранее созданному намерению",
                 now_db(), actor, point_code, amount, now_db()),
            )
        audit_log(
            conn=conn, operator_id=actor, user_id=actor, actor_type="operator",
            action_type="service_interest_cash_handover_claimed",
            table_name="service_interest_intake", row_id=int(interest_id),
            field_name="claimed_cashbox,claimed_amount", old_value=old_value,
            new_value=f"{point_code},{amount:.2f}", source_context="service_interest_intake_core",
            comment=source_note or "Заявление о передаче денег; не платёж.", commit=False,
        )
        if owns:
            conn.commit()
        return {"interest_id": int(interest_id), "point_code": point_code, "amount": amount}
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def record_external_interest(
    *, apartment_number: str, quantity: int, original_message: str,
    source_channel: str, entered_by: str, service_item_code: str = "REMOTE_NEW",
    sender_label: str = "", source_reference: str = "",
    message_received_at: str = "", claimed_cash_handover: bool = False,
    claimed_cashbox: str = "", allow_duplicate: bool = False,
    resident_account_id: int | None = None,
    auto_resolve_telegram: bool = True,
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
                    "Пункт приёма не найден среди действующих K1–K6/O или у кассира KAS1/KAS2 нет "
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
        account = (resolve_apartment_telegram_account(
            apartment_id=int(units[0]["id"]), apartment_number=apartment_number,
            resident_account_id=resident_account_id, conn=conn,
        ) if auto_resolve_telegram or resident_account_id is not None else None)
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
            resident_account_id=int(account["id"]) if account else None,
            telegram_user_id=account["telegram_user_id"] if account else None,
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
            comment=(f"Канал {source_channel}; сообщение сохранено отдельно; "
                     f"Telegram ID: {account['telegram_user_id'] if account else 'не определён'}; "
                     "заявление об оплате не подтверждено."),
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
