"""Resolve a Telegram resident by account, never guess from apartment alone."""

from __future__ import annotations

import sqlite3

from service_orders_core import get_conn
from audit_logger import audit_log


def apartment_telegram_accounts(*, apartment_id: int,
                                apartment_number: str = "",
                                conn: sqlite3.Connection | None = None) -> list[dict]:
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            """SELECT id,telegram_user_id,telegram_username,telegram_first_name,
                      telegram_last_name,status,apartment_id,apartment_number
               FROM resident_accounts
               WHERE telegram_user_id IS NOT NULL
                 AND status='apartment_confirmed'
                 AND (apartment_id=? OR (apartment_id IS NULL AND apartment_number=?))
               ORDER BY id""", (int(apartment_id), str(apartment_number).strip()),
        )]
    finally:
        if owns:
            conn.close()


def resolve_apartment_telegram_account(*, apartment_id: int,
                                       apartment_number: str = "",
                                       resident_account_id: int | None = None,
                                       conn: sqlite3.Connection | None = None) -> dict | None:
    """Return the sole confirmed account, or an explicitly selected member."""
    accounts = apartment_telegram_accounts(
        apartment_id=apartment_id, apartment_number=apartment_number, conn=conn,
    )
    if resident_account_id is not None:
        selected = next((row for row in accounts if int(row["id"]) == int(resident_account_id)), None)
        if not selected:
            raise ValueError("Выбранный Telegram-пользователь не подтверждён для этой квартиры.")
        return selected
    return accounts[0] if len(accounts) == 1 else None


def link_interest_telegram_recipient(*, interest_id: int, resident_account_id: int,
                                     actor: str, conn: sqlite3.Connection | None = None) -> dict:
    """Explicitly address an existing outside-channel interest and its order."""
    actor = actor.strip()
    if not actor:
        raise ValueError("Укажите исполнителя привязки.")
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        if owns:
            conn.execute("BEGIN IMMEDIATE")
        interest = conn.execute("SELECT * FROM service_order_interests WHERE id=?",
                                (int(interest_id),)).fetchone()
        if not interest:
            raise ValueError("Намерение не найдено.")
        account = resolve_apartment_telegram_account(
            apartment_id=int(interest["apartment_id"]),
            apartment_number=str(interest["apartment_number"]),
            resident_account_id=int(resident_account_id), conn=conn,
        )
        if interest["resident_account_id"] and int(interest["resident_account_id"]) != int(account["id"]):
            raise ValueError("У намерения уже другая учётная запись жителя.")
        if interest["telegram_user_id"] and str(interest["telegram_user_id"]) != str(account["telegram_user_id"]):
            raise ValueError("У намерения уже другой Telegram-адресат; автоматическая замена запрещена.")
        order_id = interest["service_order_id"]
        if order_id:
            order = conn.execute("SELECT resident_account_id,telegram_user_id FROM service_orders WHERE id=?",
                                 (int(order_id),)).fetchone()
            if not order:
                raise ValueError("Связанный заказ не найден.")
            if order["resident_account_id"] and int(order["resident_account_id"]) != int(account["id"]):
                raise ValueError("У заказа уже другая учётная запись жителя.")
            if order["telegram_user_id"] and str(order["telegram_user_id"]) != str(account["telegram_user_id"]):
                raise ValueError("У заказа уже другой Telegram-адресат; автоматическая замена запрещена.")
        conn.execute(
            "UPDATE service_order_interests SET resident_account_id=?,telegram_user_id=? WHERE id=?",
            (int(account["id"]), str(account["telegram_user_id"]), int(interest_id)),
        )
        if order_id:
            conn.execute(
                "UPDATE service_orders SET resident_account_id=?,telegram_user_id=? WHERE id=?",
                (int(account["id"]), str(account["telegram_user_id"]), int(order_id)),
            )
        if interest["payment_id"]:
            from cashier_v2_core import ensure_cash_telegram_fields
            ensure_cash_telegram_fields(conn.cursor())
            for table, condition, key in (
                ("payments", "id=?", int(interest["payment_id"])),
                ("cashier_receipts", "payment_id=?", int(interest["payment_id"])),
            ):
                rows = conn.execute(
                    f"SELECT resident_account_id,telegram_user_id FROM {table} WHERE {condition}", (key,)
                ).fetchall()
                for row in rows:
                    if row["resident_account_id"] and int(row["resident_account_id"]) != int(account["id"]):
                        raise ValueError("У финансовой записи уже другая учётная запись жителя.")
                    if row["telegram_user_id"] and str(row["telegram_user_id"]) != str(account["telegram_user_id"]):
                        raise ValueError("У финансовой записи уже другой Telegram-адресат.")
                conn.execute(
                    f"""UPDATE {table} SET resident_account_id=?,telegram_user_id=?
                       WHERE {condition} AND telegram_user_id IS NULL""",
                    (int(account["id"]), str(account["telegram_user_id"]), key),
                )
        audit_log(
            conn=conn, operator_id=actor, user_id=actor, actor_type="operator",
            action_type="service_interest_telegram_recipient_linked",
            table_name="service_order_interests", row_id=int(interest_id),
            field_name="resident_account_id,telegram_user_id",
            old_value=f"{interest['resident_account_id'] or ''},{interest['telegram_user_id'] or ''}",
            new_value=f"{account['id']},{account['telegram_user_id']}",
            source_context="resident_identity_core",
            comment="Оператор выбрал подтверждённого пользователя этой квартиры как адресата заказа.",
            commit=False,
        )
        if order_id and interest["interest_status"] == "PAID_ORDER_CREATED":
            from service_order_notifications import enqueue_paid_order_confirmation
            enqueue_paid_order_confirmation(int(order_id), conn=conn)
        if owns:
            conn.commit()
        return {"interest_id": int(interest_id), "service_order_id": order_id,
                "resident_account_id": int(account["id"]),
                "telegram_user_id": str(account["telegram_user_id"])}
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()
