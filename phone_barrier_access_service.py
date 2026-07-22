# -*- coding: utf-8 -*-
"""
Integration layer for the two-barrier phone-access model.

This module binds the generic paid-service pipeline to the dedicated
phone-access tables. It does not send real controller commands.
"""

from __future__ import annotations

from datetime import date, datetime
import sqlite3
from typing import Iterable

from phone_barrier_access_core import (
    activate_phone_access_subscription_for_order,
    create_phone_access_request_from_interest,
    ensure_phone_access_operational_schema,
    normalise_access_point_codes,
    normalise_phone_number,
    phone_access_request_summary,
    phone_access_subscription_summary_for_order,
    promote_paid_phone_access_request,
    quote_phone_barrier_access,
)
from service_orders_core import get_conn, text
from service_preorders_core import create_service_interest, ensure_simplified_service_schema

try:
    from service_access_policy import ensure_service_order_allowed
except Exception:
    ensure_service_order_allowed = None


def create_phone_barrier_access_interest(
    *,
    resident_account_id: int | None,
    telegram_user_id: int | str | None,
    apartment_id: int | None,
    apartment_number: str,
    service_item_code: str,
    phone: str,
    access_point_codes: Iterable[str],
    parking_debt_check_mode: str = "MANUAL_REVIEW",
    parking_debt_check_note: str = "",
    quote_date: str | date | datetime | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """
    Record one resident intention, but calculate connection price from the
    dedicated effective-dated access tariffs.

    The underlying generic interest keeps quantity = number of selected
    barriers solely to preserve payment/reconciliation compatibility. The
    actual business selection and per-barrier tariff snapshots are stored in
    phone_access_requests and phone_access_request_points.
    """
    points = normalise_access_point_codes(access_point_codes)
    phone = normalise_phone_number(phone)
    owns = conn is None
    conn = conn or get_conn()
    try:
        ensure_simplified_service_schema(conn)
        ensure_phone_access_operational_schema(conn)
        quote = quote_phone_barrier_access(
            access_point_codes=points,
            registration_date=quote_date,
            conn=conn,
        )

        # OSBB_PHONE_ACCESS_POLICY_V1
        # Business Policy must be checked before a phone-access interest is created.
        # BLOCK/ERROR raises ServiceAccessDenied inside ensure_service_order_allowed().
        # WARN/ALLOW returns and interest creation continues.
        if ensure_service_order_allowed is None:
            raise RuntimeError(
                "Critical component 'service_access_policy' is unavailable. "
                "Phone access interest creation cannot continue."
            )

        policy_result = ensure_service_order_allowed(
            conn=conn,
            apartment_number=text(apartment_number),
            service_item_code=service_item_code,
        )

        quantity = len(points)
        unit_price = round(float(quote["connection_total"]) / quantity, 2)
        interest = create_service_interest(
            resident_account_id=resident_account_id,
            telegram_user_id=telegram_user_id,
            apartment_id=apartment_id,
            apartment_number=text(apartment_number),
            service_item_code=service_item_code,
            quantity=quantity,
            resident_comment=(
                f"ACCESS_PHONE={phone}\n"
                f"ACCESS_POINTS={','.join(points)}\n"
                f"ACCESS_MODEL=PHONE_BARRIER_V2"
            ),
            service_name_snapshot_override="Телефонний доступ до шлагбаума(ів)",
            unit_price_snapshot_override=unit_price,
            amount_due_snapshot_override=float(quote["connection_total"]),
            currency_snapshot_override=text(quote["currency"]) or "UAH",
            conn=conn,
        )
        request = create_phone_access_request_from_interest(
            interest=interest,
            phone=phone,
            quote=quote,
            parking_debt_check_mode=parking_debt_check_mode,
            parking_debt_check_note=parking_debt_check_note,
            conn=conn,
        )
        result = dict(interest)
        result["phone_access_request"] = request
        result["phone_access_quote"] = quote
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


def promote_paid_phone_barrier_access_interest(
    *,
    interest: dict,
    order: dict,
    payment_id: int,
    conn: sqlite3.Connection,
) -> dict | None:
    """No-op for ordinary services; create subscription for structured phone requests."""
    ensure_phone_access_operational_schema(conn)
    return promote_paid_phone_access_request(
        interest=interest,
        order=order,
        payment_id=int(payment_id),
        conn=conn,
    )


def phone_access_summary_for_interest(
    interest_id: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict | None:
    owns = conn is None
    conn = conn or get_conn()
    try:
        ensure_phone_access_operational_schema(conn)
        return phone_access_request_summary(interest_id=int(interest_id), conn=conn)
    finally:
        if owns:
            conn.close()


def phone_access_summary_for_order(
    order_id: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict | None:
    owns = conn is None
    conn = conn or get_conn()
    try:
        ensure_phone_access_operational_schema(conn)
        summary = phone_access_request_summary(order_id=int(order_id), conn=conn)
        if summary:
            return summary
        subscription = phone_access_subscription_summary_for_order(
            order_id=int(order_id), conn=conn
        )
        if subscription:
            return {"subscription": subscription, "subscription_points": subscription["points"], "charges": subscription["charges"]}
        return None
    finally:
        if owns:
            conn.close()


def activate_phone_barrier_access_order(
    *,
    order_id: int,
    actor_id: int | str | None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    owns = conn is None
    conn = conn or get_conn()
    try:
        ensure_phone_access_operational_schema(conn)
        result = activate_phone_access_subscription_for_order(
            order_id=int(order_id),
            actor_id=actor_id,
            conn=conn,
        )
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


def phone_barrier_quote(
    *,
    access_point_codes: Iterable[str],
    quote_date: str | date | datetime | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    owns = conn is None
    conn = conn or get_conn()
    try:
        ensure_phone_access_operational_schema(conn)
        return quote_phone_barrier_access(
            access_point_codes=access_point_codes,
            registration_date=quote_date,
            conn=conn,
        )
    finally:
        if owns:
            conn.close()
