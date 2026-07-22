# -*- coding: utf-8 -*-
"""
OSBB phone access to two barriers — additive schema and policy core.

This module is intentionally schema/business-rule only. It does NOT:
- send commands to real barriers;
- modify existing service orders, payments, phone credentials or remotes;
- create recurring charges automatically;
- deactivate any existing phone access.

It creates the additive tables needed for a later sandbox implementation:
- access points;
- effective-dated tariffs and policies;
- phone-access subscriptions;
- per-barrier access grants;
- subscription charges;
- debt warnings;
- external controller-command journal;
- immutable operating journal.

All prices and debt-deactivation terms are data, never hard-coded in workflows.
"""

from __future__ import annotations

import calendar
import json
import re
import sqlite3
from datetime import date, datetime
from typing import Any, Iterable


SCHEMA_MIGRATION_CODE = "PHONE_BARRIER_ACCESS_SCHEMA_V1"

ACCESS_POLICY_SET = "PHONE_BARRIER_ACCESS"

BARRIER_FAR_01 = "BARRIER_FAR_01"
BARRIER_NEAR_02 = "BARRIER_NEAR_02"
GENERIC_ACCESS_POINT_SCOPE = "*"

TARIFF_CONNECT = "PHONE_BARRIER_ACCESS_CONNECT"
TARIFF_MONTHLY = "PHONE_BARRIER_ACCESS_MONTHLY"

POLICY_DEBT_GRACE_DAYS = "PHONE_ACCESS_DEBT_GRACE_DAYS"
POLICY_MONTHLY_START_RULE = "PHONE_ACCESS_MONTHLY_START_RULE"
POLICY_AUTO_DEACTIVATE_ENABLED = "PHONE_ACCESS_AUTO_DEACTIVATE_ENABLED"
POLICY_DEFAULT_PARKING_DEBT_MODE = "PHONE_ACCESS_DEFAULT_PARKING_DEBT_MODE"
POLICY_REAPPLICATION_NEW_SUBSCRIPTION = "PHONE_ACCESS_REAPPLICATION_NEW_SUBSCRIPTION"

MONTHLY_START_RULE = "MIDPOINT_OF_CALENDAR_MONTH"


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def date_db() -> str:
    return date.today().isoformat()


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    )
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    cur.execute(f"PRAGMA table_info({quote(table)})")
    return {str(row[1]) for row in cur.fetchall()}


def as_dict(row: sqlite3.Row | tuple | None, columns: Iterable[str] = ()) -> dict | None:
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    names = list(columns)
    if not names:
        raise TypeError("A tuple row needs explicit column names.")
    return dict(zip(names, row))


def parse_iso_date(value: str | date | datetime | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(text(value))


def first_charge_period(activation_date: str | date | datetime) -> str:
    """
    Return YYYY-MM according to the approved midpoint rule.

    A registration on or before half of the calendar month is charged for
    the current month. A registration after that half starts next month.

    Exact rule:
        day * 2 <= number_of_days_in_month -> current month
        day * 2 >  number_of_days_in_month -> next month
    """
    activated = parse_iso_date(activation_date)
    days_in_month = calendar.monthrange(activated.year, activated.month)[1]
    if activated.day * 2 <= days_in_month:
        return f"{activated.year:04d}-{activated.month:02d}"
    if activated.month == 12:
        return f"{activated.year + 1:04d}-01"
    return f"{activated.year:04d}-{activated.month + 1:02d}"


def required_phone_access_tables() -> set[str]:
    return {
        "access_schema_migrations",
        "access_points",
        "access_tariff_versions",
        "access_policy_versions",
        "access_policy_values",
        "phone_access_subscriptions",
        "phone_access_subscription_points",
        "phone_access_subscription_charges",
        "access_debt_warnings",
        "access_external_commands",
        "access_operation_journal",
    }


def _fetchone_dict(
    cur: sqlite3.Cursor,
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict | None:
    cur.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    cols = [str(item[0]) for item in cur.description or []]
    return dict(zip(cols, row))


def _created_tables(cur: sqlite3.Cursor) -> set[str]:
    return {
        str(row[0])
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _create_schema(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS access_schema_migrations (
            migration_code TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            applied_by TEXT,
            sandbox_db_path TEXT,
            note TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS access_points (
            id INTEGER PRIMARY KEY,
            access_point_code TEXT NOT NULL UNIQUE,
            display_name_uk TEXT NOT NULL,
            display_name_ru TEXT NOT NULL,
            display_name_en TEXT NOT NULL,
            point_status TEXT NOT NULL DEFAULT 'ACTIVE'
                CHECK(point_status IN ('DRAFT', 'ACTIVE', 'RETIRED', 'ARCHIVED')),
            controller_type TEXT,
            controller_reference TEXT,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            retired_at TEXT,
            retired_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_access_points_active
        ON access_points(is_active, point_status, access_point_code)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS access_tariff_versions (
            id INTEGER PRIMARY KEY,
            tariff_code TEXT NOT NULL,
            tariff_name_uk TEXT NOT NULL,
            tariff_name_ru TEXT NOT NULL,
            tariff_name_en TEXT NOT NULL,
            access_point_scope_code TEXT NOT NULL DEFAULT '*',
            charge_kind TEXT NOT NULL
                CHECK(charge_kind IN ('CONNECT', 'MONTHLY')),
            billing_period TEXT NOT NULL
                CHECK(billing_period IN ('ONE_TIME', 'MONTHLY')),
            unit_of_measure TEXT NOT NULL DEFAULT 'PER_ACCESS_POINT',
            amount REAL NOT NULL CHECK(amount >= 0),
            currency TEXT NOT NULL DEFAULT 'UAH',
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            version_status TEXT NOT NULL DEFAULT 'ACTIVE'
                CHECK(version_status IN ('DRAFT', 'ACTIVE', 'RETIRED', 'ARCHIVED')),
            approved_by TEXT,
            approval_reference TEXT,
            change_reason TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(tariff_code, access_point_scope_code, effective_from)
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_access_tariff_lookup
        ON access_tariff_versions(
            tariff_code, access_point_scope_code, version_status, effective_from, effective_to
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS access_policy_versions (
            id INTEGER PRIMARY KEY,
            policy_set_code TEXT NOT NULL,
            version_number INTEGER NOT NULL CHECK(version_number > 0),
            policy_status TEXT NOT NULL DEFAULT 'ACTIVE'
                CHECK(policy_status IN ('DRAFT', 'ACTIVE', 'RETIRED', 'ARCHIVED')),
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            approved_by TEXT,
            approval_reference TEXT,
            change_reason TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(policy_set_code, version_number),
            UNIQUE(policy_set_code, effective_from)
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_access_policy_lookup
        ON access_policy_versions(policy_set_code, policy_status, effective_from, effective_to)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS access_policy_values (
            id INTEGER PRIMARY KEY,
            policy_version_id INTEGER NOT NULL,
            setting_code TEXT NOT NULL,
            value_type TEXT NOT NULL
                CHECK(value_type IN ('TEXT', 'INTEGER', 'BOOLEAN', 'JSON')),
            value_text TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(policy_version_id, setting_code)
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_access_policy_values_code
        ON access_policy_values(setting_code, policy_version_id)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS phone_access_subscriptions (
            id INTEGER PRIMARY KEY,
            subscription_number TEXT NOT NULL UNIQUE,
            resident_account_id INTEGER,
            authorised_person_id INTEGER,
            unit_id INTEGER,
            apartment_id INTEGER,
            apartment_number TEXT,
            holder_type TEXT NOT NULL DEFAULT 'RESIDENT'
                CHECK(holder_type IN ('RESIDENT', 'TENANT', 'AUTHORISED_PERSON')),
            phone_normalized TEXT NOT NULL,
            subscription_status TEXT NOT NULL DEFAULT 'PENDING_ACTIVATION'
                CHECK(subscription_status IN (
                    'PENDING_ACTIVATION', 'ACTIVE', 'PARTIALLY_ACTIVE',
                    'DEBT_WARNING', 'DEACTIVATING', 'DEACTIVATED_FOR_DEBT',
                    'DEACTIVATED_MANUAL', 'CLOSED', 'CANCELLED'
                )),
            parking_debt_check_mode TEXT NOT NULL DEFAULT 'MANUAL_REVIEW'
                CHECK(parking_debt_check_mode IN (
                    'CHECK_LINKED_PARKING_ACCOUNT',
                    'NOT_APPLICABLE_NO_PARKING',
                    'MANUAL_REVIEW'
                )),
            parking_debt_check_note TEXT,
            parking_debt_mode_set_by TEXT,
            parking_debt_mode_set_at TEXT,
            activation_date TEXT,
            first_charge_period TEXT,
            monthly_start_policy_version_id INTEGER,
            connect_tariff_version_id INTEGER,
            monthly_tariff_version_id INTEGER,
            created_from_order_id INTEGER,
            created_from_interest_id INTEGER,
            closed_at TEXT,
            close_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_phone_access_subscriptions_status
        ON phone_access_subscriptions(subscription_status, apartment_id, phone_normalized)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_phone_access_subscriptions_phone
        ON phone_access_subscriptions(phone_normalized, subscription_status)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS phone_access_subscription_points (
            id INTEGER PRIMARY KEY,
            subscription_id INTEGER NOT NULL,
            access_point_code TEXT NOT NULL,
            access_point_name_snapshot TEXT NOT NULL,
            point_status TEXT NOT NULL DEFAULT 'PENDING_ACTIVATION'
                CHECK(point_status IN (
                    'PENDING_ACTIVATION', 'ACTIVE', 'SUSPENDED_FOR_DEBT',
                    'DEACTIVATED_FOR_DEBT', 'DEACTIVATED_MANUAL',
                    'EXTERNAL_SYNC_ERROR', 'CANCELLED'
                )),
            activated_at TEXT,
            deactivated_at TEXT,
            deactivation_reason TEXT,
            external_controller_reference TEXT,
            last_external_sync_status TEXT,
            last_external_sync_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(subscription_id, access_point_code)
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_phone_access_points_status
        ON phone_access_subscription_points(subscription_id, point_status, access_point_code)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS phone_access_subscription_charges (
            id INTEGER PRIMARY KEY,
            charge_number TEXT NOT NULL UNIQUE,
            subscription_id INTEGER NOT NULL,
            subscription_point_id INTEGER,
            access_point_code TEXT NOT NULL,
            charge_kind TEXT NOT NULL
                CHECK(charge_kind IN ('CONNECT', 'MONTHLY')),
            charge_period_key TEXT NOT NULL,
            billing_period TEXT,
            tariff_code TEXT NOT NULL,
            tariff_version_id INTEGER,
            quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity > 0),
            unit_price_snapshot REAL NOT NULL CHECK(unit_price_snapshot >= 0),
            amount_due_snapshot REAL NOT NULL CHECK(amount_due_snapshot >= 0),
            currency TEXT NOT NULL DEFAULT 'UAH',
            due_date TEXT,
            charge_status TEXT NOT NULL DEFAULT 'DRAFT'
                CHECK(charge_status IN (
                    'DRAFT', 'OPEN', 'PAID', 'OVERDUE', 'WAIVED', 'CANCELLED'
                )),
            service_order_id INTEGER,
            payment_notice_id INTEGER,
            payment_id INTEGER,
            posted_at TEXT,
            paid_at TEXT,
            cancelled_at TEXT,
            cancellation_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(subscription_point_id, charge_kind, charge_period_key)
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_phone_access_charges_due
        ON phone_access_subscription_charges(subscription_id, charge_status, due_date, billing_period)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_phone_access_charges_payment
        ON phone_access_subscription_charges(payment_id, payment_notice_id)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS access_debt_warnings (
            id INTEGER PRIMARY KEY,
            warning_number TEXT NOT NULL UNIQUE,
            subscription_id INTEGER NOT NULL,
            debt_source TEXT NOT NULL
                CHECK(debt_source IN (
                    'PARKING_ARREARS',
                    'PHONE_ACCESS_SUBSCRIPTION_ARREARS'
                )),
            source_charge_id INTEGER,
            debt_amount_snapshot REAL NOT NULL CHECK(debt_amount_snapshot >= 0),
            currency TEXT NOT NULL DEFAULT 'UAH',
            source_reference TEXT,
            detected_at TEXT NOT NULL,
            warning_sent_at TEXT,
            grace_days_snapshot INTEGER NOT NULL CHECK(grace_days_snapshot >= 0),
            deactivate_due_at TEXT NOT NULL,
            policy_version_id INTEGER,
            warning_status TEXT NOT NULL DEFAULT 'OPEN'
                CHECK(warning_status IN (
                    'OPEN', 'RESOLVED', 'CANCELLED',
                    'DEACTIVATION_QUEUED', 'DEACTIVATED'
                )),
            resolved_at TEXT,
            resolution_note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_access_debt_warnings_due
        ON access_debt_warnings(warning_status, deactivate_due_at, subscription_id)
        """
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_access_debt_warning_open
        ON access_debt_warnings(subscription_id, debt_source)
        WHERE warning_status IN ('OPEN', 'DEACTIVATION_QUEUED')
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS access_external_commands (
            id INTEGER PRIMARY KEY,
            command_number TEXT NOT NULL UNIQUE,
            subscription_id INTEGER NOT NULL,
            subscription_point_id INTEGER NOT NULL,
            access_point_code TEXT NOT NULL,
            command_action TEXT NOT NULL
                CHECK(command_action IN ('ACTIVATE', 'DEACTIVATE')),
            phone_snapshot TEXT NOT NULL,
            command_status TEXT NOT NULL DEFAULT 'QUEUED'
                CHECK(command_status IN (
                    'QUEUED', 'SENT', 'CONFIRMED', 'FAILED',
                    'RETRY_SCHEDULED', 'CANCELLED'
                )),
            attempt_number INTEGER NOT NULL DEFAULT 0 CHECK(attempt_number >= 0),
            external_correlation_id TEXT,
            payload_json TEXT,
            last_error TEXT,
            queued_at TEXT NOT NULL,
            sent_at TEXT,
            confirmed_at TEXT,
            failed_at TEXT,
            retry_at TEXT,
            created_by TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_access_external_commands_queue
        ON access_external_commands(command_status, retry_at, access_point_code, id)
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS access_operation_journal (
            id INTEGER PRIMARY KEY,
            event_number TEXT NOT NULL UNIQUE,
            subscription_id INTEGER,
            subscription_point_id INTEGER,
            access_point_code TEXT,
            event_type TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT,
            actor_id TEXT,
            source_context TEXT,
            correlation_id TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_access_operation_journal_subscription
        ON access_operation_journal(subscription_id, created_at, id)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_access_operation_journal_point
        ON access_operation_journal(subscription_point_id, created_at, id)
        """
    )


def _record_change(
    cur: sqlite3.Cursor,
    *,
    event_number: str,
    event_type: str,
    source_context: str,
    payload: dict[str, Any],
    actor_id: str = "schema_migration",
) -> None:
    cur.execute(
        """
        INSERT OR IGNORE INTO access_operation_journal (
            event_number, event_type, actor_id, source_context, payload_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event_number,
            event_type,
            actor_id,
            source_context,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            now_db(),
        ),
    )


def _ensure_access_point(
    cur: sqlite3.Cursor,
    *,
    code: str,
    uk: str,
    ru: str,
    en: str,
    effective_from: str,
) -> bool:
    existing = _fetchone_dict(
        cur,
        "SELECT * FROM access_points WHERE access_point_code = ?",
        (code,),
    )
    if existing:
        return False
    cur.execute(
        """
        INSERT INTO access_points (
            access_point_code, display_name_uk, display_name_ru, display_name_en,
            point_status, is_active, effective_from, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'ACTIVE', 1, ?, ?, ?)
        """,
        (code, uk, ru, en, effective_from, now_db(), now_db()),
    )
    return True


def _ensure_tariff(
    cur: sqlite3.Cursor,
    *,
    tariff_code: str,
    uk: str,
    ru: str,
    en: str,
    charge_kind: str,
    billing_period: str,
    amount: float,
    effective_from: str,
    created_by: str,
) -> bool:
    existing = _fetchone_dict(
        cur,
        """
        SELECT *
        FROM access_tariff_versions
        WHERE tariff_code = ?
          AND access_point_scope_code = ?
          AND effective_from = ?
        """,
        (tariff_code, GENERIC_ACCESS_POINT_SCOPE, effective_from),
    )
    if existing:
        expected = (float(amount), charge_kind, billing_period, "UAH")
        actual = (
            float(existing["amount"]),
            text(existing["charge_kind"]),
            text(existing["billing_period"]),
            text(existing["currency"]),
        )
        if actual != expected:
            raise RuntimeError(
                "Найдена существующая тарифная версия с тем же кодом и датой, "
                f"но другими данными: {tariff_code} / {effective_from}."
            )
        return False

    cur.execute(
        """
        INSERT INTO access_tariff_versions (
            tariff_code, tariff_name_uk, tariff_name_ru, tariff_name_en,
            access_point_scope_code, charge_kind, billing_period, unit_of_measure,
            amount, currency, effective_from, version_status, approval_reference,
            change_reason, created_by, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'PER_ACCESS_POINT',
                ?, 'UAH', ?, 'ACTIVE', ?, ?, ?, ?, ?)
        """,
        (
            tariff_code,
            uk,
            ru,
            en,
            GENERIC_ACCESS_POINT_SCOPE,
            charge_kind,
            billing_period,
            float(amount),
            effective_from,
            "SANDBOX_INITIAL_SEED",
            "Initial agreed phone-barrier tariff for sandbox.",
            created_by,
            now_db(),
            now_db(),
        ),
    )
    return True


def _ensure_policy_version(
    cur: sqlite3.Cursor,
    *,
    effective_from: str,
    created_by: str,
) -> tuple[int, bool]:
    existing = _fetchone_dict(
        cur,
        """
        SELECT *
        FROM access_policy_versions
        WHERE policy_set_code = ? AND effective_from = ?
        """,
        (ACCESS_POLICY_SET, effective_from),
    )
    if existing:
        return int(existing["id"]), False

    cur.execute(
        """
        SELECT COALESCE(MAX(version_number), 0) + 1
        FROM access_policy_versions
        WHERE policy_set_code = ?
        """,
        (ACCESS_POLICY_SET,),
    )
    version_number = int(cur.fetchone()[0])
    cur.execute(
        """
        INSERT INTO access_policy_versions (
            policy_set_code, version_number, policy_status, effective_from,
            approval_reference, change_reason, created_by, created_at, updated_at
        )
        VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?)
        """,
        (
            ACCESS_POLICY_SET,
            version_number,
            effective_from,
            "SANDBOX_INITIAL_SEED",
            "Initial agreed phone-barrier policy for sandbox.",
            created_by,
            now_db(),
            now_db(),
        ),
    )
    return int(cur.lastrowid), True


def _ensure_policy_value(
    cur: sqlite3.Cursor,
    *,
    policy_version_id: int,
    setting_code: str,
    value_type: str,
    value_text: str,
    description: str,
) -> bool:
    existing = _fetchone_dict(
        cur,
        """
        SELECT *
        FROM access_policy_values
        WHERE policy_version_id = ? AND setting_code = ?
        """,
        (int(policy_version_id), setting_code),
    )
    if existing:
        expected = (value_type, value_text)
        actual = (text(existing["value_type"]), text(existing["value_text"]))
        if actual != expected:
            raise RuntimeError(
                "Найдена существующая настройка политики с другим значением: "
                f"{setting_code}."
            )
        return False
    cur.execute(
        """
        INSERT INTO access_policy_values (
            policy_version_id, setting_code, value_type, value_text,
            description, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(policy_version_id),
            setting_code,
            value_type,
            value_text,
            description,
            now_db(),
            now_db(),
        ),
    )
    return True


def active_policy_values(
    *,
    as_of: str | date | datetime | None = None,
    conn: sqlite3.Connection,
) -> dict[str, str]:
    """Return the active phone-barrier policy values for a given date."""
    when = parse_iso_date(as_of).isoformat()
    cur = conn.cursor()
    row = _fetchone_dict(
        cur,
        """
        SELECT *
        FROM access_policy_versions
        WHERE policy_set_code = ?
          AND policy_status = 'ACTIVE'
          AND effective_from <= ?
          AND (effective_to IS NULL OR effective_to >= ?)
        ORDER BY effective_from DESC, version_number DESC
        LIMIT 1
        """,
        (ACCESS_POLICY_SET, when, when),
    )
    if not row:
        raise LookupError("Не найдена активная политика телефонного доступа.")
    cur.execute(
        """
        SELECT setting_code, value_text
        FROM access_policy_values
        WHERE policy_version_id = ?
        ORDER BY setting_code
        """,
        (int(row["id"]),),
    )
    return {text(item[0]): text(item[1]) for item in cur.fetchall()}


def resolve_tariff(
    *,
    tariff_code: str,
    access_point_code: str,
    as_of: str | date | datetime | None = None,
    conn: sqlite3.Connection,
) -> dict:
    """
    Return a current tariff snapshot.

    A tariff specifically assigned to the selected access point wins over
    the generic '*' tariff. This supports future price differences without
    redesigning the schema.
    """
    when = parse_iso_date(as_of).isoformat()
    cur = conn.cursor()
    row = _fetchone_dict(
        cur,
        """
        SELECT *
        FROM access_tariff_versions
        WHERE tariff_code = ?
          AND access_point_scope_code IN (?, ?)
          AND version_status = 'ACTIVE'
          AND effective_from <= ?
          AND (effective_to IS NULL OR effective_to >= ?)
        ORDER BY
          CASE WHEN access_point_scope_code = ? THEN 0 ELSE 1 END,
          effective_from DESC,
          id DESC
        LIMIT 1
        """,
        (
            tariff_code,
            access_point_code,
            GENERIC_ACCESS_POINT_SCOPE,
            when,
            when,
            access_point_code,
        ),
    )
    if not row:
        raise LookupError(
            f"Не найден активный тариф {tariff_code} для {access_point_code}."
        )
    return row


def ensure_phone_barrier_access_schema(
    conn: sqlite3.Connection,
    *,
    effective_from: str | date | datetime | None = None,
    actor_id: str = "schema_migration",
    sandbox_db_path: str = "",
) -> list[str]:
    """
    Create additive tables and seed only the agreed initial sandbox values.

    The caller owns the transaction. No commit/rollback is performed here.
    """
    effective = parse_iso_date(effective_from).isoformat()
    cur = conn.cursor()
    before = _created_tables(cur)
    _create_schema(cur)
    after = _created_tables(cur)

    changes: list[str] = []
    created = sorted(required_phone_access_tables() - before)
    for table in created:
        changes.append(f"created table {table}")

    if _ensure_access_point(
        cur,
        code=BARRIER_FAR_01,
        uk="Далекий шлагбаум №1",
        ru="Дальний шлагбаум №1",
        en="Far barrier No. 1",
        effective_from=effective,
    ):
        changes.append(f"seeded access point {BARRIER_FAR_01}")

    if _ensure_access_point(
        cur,
        code=BARRIER_NEAR_02,
        uk="Ближній шлагбаум №2",
        ru="Ближний шлагбаум №2",
        en="Near barrier No. 2",
        effective_from=effective,
    ):
        changes.append(f"seeded access point {BARRIER_NEAR_02}")

    if _ensure_tariff(
        cur,
        tariff_code=TARIFF_CONNECT,
        uk="Підключення телефону до одного шлагбаума",
        ru="Подключение телефона к одному шлагбауму",
        en="Phone connection to one barrier",
        charge_kind="CONNECT",
        billing_period="ONE_TIME",
        amount=200.0,
        effective_from=effective,
        created_by=actor_id,
    ):
        changes.append("seeded tariff PHONE_BARRIER_ACCESS_CONNECT = 200 UAH per barrier")

    if _ensure_tariff(
        cur,
        tariff_code=TARIFF_MONTHLY,
        uk="Абонплата за телефонний доступ до одного шлагбаума",
        ru="Абонплата за телефонный доступ к одному шлагбауму",
        en="Monthly phone access for one barrier",
        charge_kind="MONTHLY",
        billing_period="MONTHLY",
        amount=100.0,
        effective_from=effective,
        created_by=actor_id,
    ):
        changes.append("seeded tariff PHONE_BARRIER_ACCESS_MONTHLY = 100 UAH per barrier/month")

    policy_id, policy_created = _ensure_policy_version(
        cur,
        effective_from=effective,
        created_by=actor_id,
    )
    if policy_created:
        changes.append(
            f"seeded policy {ACCESS_POLICY_SET} version for {effective}"
        )

    policy_values = [
        (
            POLICY_DEBT_GRACE_DAYS,
            "INTEGER",
            "10",
            "Grace period after a confirmed debt warning, in calendar days.",
        ),
        (
            POLICY_MONTHLY_START_RULE,
            "TEXT",
            MONTHLY_START_RULE,
            "Current month up to the month midpoint; next month after it.",
        ),
        (
            POLICY_AUTO_DEACTIVATE_ENABLED,
            "BOOLEAN",
            "1",
            "After the grace deadline, deactivate all selected access points.",
        ),
        (
            POLICY_DEFAULT_PARKING_DEBT_MODE,
            "TEXT",
            "MANUAL_REVIEW",
            "Safe default: no automatic parking-debt action until verified.",
        ),
        (
            POLICY_REAPPLICATION_NEW_SUBSCRIPTION,
            "BOOLEAN",
            "1",
            "Re-access after debt deactivation creates a new subscription.",
        ),
    ]
    for code, value_type, value, description in policy_values:
        if _ensure_policy_value(
            cur,
            policy_version_id=policy_id,
            setting_code=code,
            value_type=value_type,
            value_text=value,
            description=description,
        ):
            changes.append(f"seeded policy value {code} = {value}")

    prior = _fetchone_dict(
        cur,
        "SELECT migration_code FROM access_schema_migrations WHERE migration_code = ?",
        (SCHEMA_MIGRATION_CODE,),
    )
    if not prior:
        cur.execute(
            """
            INSERT INTO access_schema_migrations (
                migration_code, schema_version, applied_at, applied_by,
                sandbox_db_path, note
            )
            VALUES (?, '1.0', ?, ?, ?, ?)
            """,
            (
                SCHEMA_MIGRATION_CODE,
                now_db(),
                actor_id,
                sandbox_db_path,
                "Additive sandbox schema for two-barrier phone access.",
            ),
        )
        changes.append(f"recorded migration {SCHEMA_MIGRATION_CODE}")
        _record_change(
            cur,
            event_number=f"MIG-{SCHEMA_MIGRATION_CODE}",
            event_type="SCHEMA_MIGRATION_APPLIED",
            source_context="phone_barrier_access_schema",
            payload={
                "migration_code": SCHEMA_MIGRATION_CODE,
                "effective_from": effective,
                "created_tables": created,
            },
            actor_id=actor_id,
        )

    return changes or ["schema and initial sandbox policy already present"]


# ---------------------------------------------------------------------------
# Operational request/subscription layer — schema migration V2
# ---------------------------------------------------------------------------
#
# V1 created the stable access points, effective-dated tariff/policy tables,
# subscription tables, charges, warnings and audit journals. V2 adds the
# durable bridge between a resident's unpaid interest and the later paid order.
#
# That bridge prevents us from using resident_comment as an accounting record:
# phone number, selected barriers, tariff snapshots and request policy are
# kept in normalized tables before payment is confirmed.

OPERATIONAL_SCHEMA_MIGRATION_CODE = "PHONE_BARRIER_ACCESS_OPERATIONAL_SCHEMA_V2"

REQUEST_INTEREST = "INTEREST"
REQUEST_PAID_ORDER_CREATED = "PAID_ORDER_CREATED"
REQUEST_ACTIVE = "ACTIVE"
REQUEST_CANCELLED = "CANCELLED"

POINT_PENDING_ACTIVATION = "PENDING_ACTIVATION"
POINT_ACTIVE = "ACTIVE"

CHARGE_CONNECT = "CONNECT"
CHARGE_MONTHLY = "MONTHLY"
CHARGE_PAID = "PAID"
CHARGE_OPEN = "OPEN"

ACCESS_STATUS_PENDING = "PENDING_ACTIVATION"
ACCESS_STATUS_ACTIVE = "ACTIVE"
ACCESS_STATUS_PARTIALLY_ACTIVE = "PARTIALLY_ACTIVE"


def _operational_tables() -> set[str]:
    return {
        "phone_access_requests",
        "phone_access_request_points",
    }


def required_phone_access_operational_tables() -> set[str]:
    return required_phone_access_tables() | _operational_tables()


def _next_number(prefix: str, cur: sqlite3.Cursor, table: str, field: str) -> str:
    day = datetime.now().strftime("%Y%m%d")
    marker = f"{prefix}-{day}-"
    cur.execute(
        f"SELECT {quote(field)} FROM {quote(table)} "
        f"WHERE {quote(field)} LIKE ? ORDER BY {quote(field)} DESC LIMIT 1",
        (marker + "%",),
    )
    row = cur.fetchone()
    serial = 1
    if row:
        try:
            serial = int(str(row[0]).rsplit("-", 1)[-1]) + 1
        except (ValueError, IndexError):
            serial = 1
    return f"{marker}{serial:06d}"


def _active_policy(
    cur: sqlite3.Cursor,
    *,
    as_of: str | date | datetime | None = None,
) -> tuple[dict, dict[str, str]]:
    when = parse_iso_date(as_of).isoformat()
    row = _fetchone_dict(
        cur,
        """
        SELECT *
        FROM access_policy_versions
        WHERE policy_set_code = ?
          AND policy_status = 'ACTIVE'
          AND effective_from <= ?
          AND (effective_to IS NULL OR effective_to >= ?)
        ORDER BY effective_from DESC, version_number DESC, id DESC
        LIMIT 1
        """,
        (ACCESS_POLICY_SET, when, when),
    )
    if not row:
        raise LookupError("Не найдена действующая политика телефонного доступа.")
    cur.execute(
        """
        SELECT setting_code, value_text
        FROM access_policy_values
        WHERE policy_version_id = ?
        ORDER BY setting_code
        """,
        (int(row["id"]),),
    )
    values = {text(item[0]): text(item[1]) for item in cur.fetchall()}
    return row, values


def _access_point_rows(
    cur: sqlite3.Cursor,
    point_codes: list[str],
) -> dict[str, dict]:
    if not point_codes:
        raise ValueError("Нужно выбрать хотя бы один шлагбаум.")
    placeholders = ",".join("?" for _ in point_codes)
    cur.execute(
        f"""
        SELECT *
        FROM access_points
        WHERE access_point_code IN ({placeholders})
          AND is_active = 1
          AND point_status = 'ACTIVE'
        """,
        tuple(point_codes),
    )
    result = {text(row["access_point_code"]): dict(row) for row in cur.fetchall()}
    missing = [code for code in point_codes if code not in result]
    if missing:
        raise ValueError(
            "Выбран неактивный или неизвестный шлагбаум: " + ", ".join(missing)
        )
    return result


def normalise_access_point_codes(point_codes: Iterable[str]) -> list[str]:
    allowed = {BARRIER_FAR_01, BARRIER_NEAR_02}
    values: list[str] = []
    for raw in point_codes:
        code = text(raw).upper()
        if code and code not in values:
            values.append(code)
    if not values:
        raise ValueError("Нужно выбрать хотя бы один шлагбаум.")
    bad = [code for code in values if code not in allowed]
    if bad:
        raise ValueError("Неизвестный шлагбаум: " + ", ".join(bad))
    return values


def normalise_phone_number(phone: str) -> str:
    value = re.sub(r"[\s()\-]", "", text(phone))
    if not re.fullmatch(r"\+?\d{8,20}", value):
        raise ValueError("Укажите телефон: 8–20 цифр, можно начать с +.")
    return value


def _access_point_name(row: dict, lang: str = "uk") -> str:
    key = {
        "ru": "display_name_ru",
        "en": "display_name_en",
    }.get(text(lang).lower(), "display_name_uk")
    return text(row.get(key)) or text(row.get("display_name_uk")) or text(
        row.get("access_point_code")
    )


def quote_phone_barrier_access(
    *,
    access_point_codes: Iterable[str],
    registration_date: str | date | datetime | None = None,
    conn: sqlite3.Connection,
) -> dict:
    """
    Produce a reproducible resident quote before an interest is written.

    The quoted connection tariff is later stored per selected barrier in
    phone_access_request_points. It therefore survives a later tariff change.
    """
    points = normalise_access_point_codes(access_point_codes)
    registered = parse_iso_date(registration_date)
    cur = conn.cursor()
    point_rows = _access_point_rows(cur, points)
    policy, values = _active_policy(cur, as_of=registered)

    selected: list[dict] = []
    currencies: set[str] = set()
    total_connect = 0.0
    total_monthly = 0.0
    for code in points:
        connect = resolve_tariff(
            tariff_code=TARIFF_CONNECT,
            access_point_code=code,
            as_of=registered,
            conn=conn,
        )
        monthly = resolve_tariff(
            tariff_code=TARIFF_MONTHLY,
            access_point_code=code,
            as_of=registered,
            conn=conn,
        )
        connect_currency = text(connect.get("currency")) or "UAH"
        monthly_currency = text(monthly.get("currency")) or "UAH"
        currencies.update({connect_currency, monthly_currency})
        total_connect += float(connect["amount"])
        total_monthly += float(monthly["amount"])
        point = point_rows[code]
        selected.append(
            {
                "access_point_code": code,
                "access_point_name_uk": _access_point_name(point, "uk"),
                "access_point_name_ru": _access_point_name(point, "ru"),
                "access_point_name_en": _access_point_name(point, "en"),
                "connect_tariff_version_id": int(connect["id"]),
                "connect_unit_price": round(float(connect["amount"]), 2),
                "monthly_tariff_version_id": int(monthly["id"]),
                "monthly_unit_price": round(float(monthly["amount"]), 2),
                "currency": connect_currency,
            }
        )
    if len(currencies) != 1:
        raise RuntimeError(
            "Тарифы выбранных шлагбаумов имеют разные валюты; единый заказ невозможен."
        )

    rule = values.get(POLICY_MONTHLY_START_RULE, "")
    if rule != MONTHLY_START_RULE:
        raise RuntimeError(
            "Для текущей политики не реализовано правило первой абонплаты: "
            + (rule or "не задано")
        )
    return {
        "access_points": selected,
        "access_point_codes": points,
        "currency": next(iter(currencies)),
        "connection_total": round(total_connect, 2),
        "monthly_total": round(total_monthly, 2),
        "registration_date": registered.isoformat(),
        "first_charge_period": first_charge_period(registered),
        "policy_version_id": int(policy["id"]),
        "policy_version_number": int(policy["version_number"]),
        "monthly_start_rule": rule,
        "grace_days": int(values.get(POLICY_DEBT_GRACE_DAYS, "10")),
        "default_parking_debt_check_mode": values.get(
            POLICY_DEFAULT_PARKING_DEBT_MODE, "MANUAL_REVIEW"
        ),
    }


def _insert_operational_schema(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS phone_access_requests (
            id INTEGER PRIMARY KEY,
            request_number TEXT NOT NULL UNIQUE,
            interest_id INTEGER NOT NULL UNIQUE,
            service_order_id INTEGER UNIQUE,
            resident_account_id INTEGER,
            telegram_user_id TEXT,
            apartment_id INTEGER,
            apartment_number TEXT NOT NULL,
            phone_normalized TEXT NOT NULL,
            request_status TEXT NOT NULL DEFAULT 'INTEREST'
                CHECK(request_status IN (
                    'INTEREST', 'PAID_ORDER_CREATED', 'ACTIVE', 'CANCELLED'
                )),
            parking_debt_check_mode TEXT NOT NULL DEFAULT 'MANUAL_REVIEW'
                CHECK(parking_debt_check_mode IN (
                    'CHECK_LINKED_PARKING_ACCOUNT',
                    'NOT_APPLICABLE_NO_PARKING',
                    'MANUAL_REVIEW'
                )),
            parking_debt_check_note TEXT,
            policy_version_id INTEGER,
            quoted_at TEXT NOT NULL,
            registered_at TEXT,
            first_charge_period TEXT,
            paid_at TEXT,
            cancelled_at TEXT,
            cancellation_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_phone_access_requests_order
        ON phone_access_requests(service_order_id, request_status)
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_phone_access_requests_phone
        ON phone_access_requests(phone_normalized, apartment_id, request_status)
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS phone_access_request_points (
            id INTEGER PRIMARY KEY,
            request_id INTEGER NOT NULL,
            access_point_code TEXT NOT NULL,
            access_point_name_snapshot TEXT NOT NULL,
            connect_tariff_version_id INTEGER,
            connect_unit_price_snapshot REAL NOT NULL CHECK(connect_unit_price_snapshot >= 0),
            monthly_tariff_version_id INTEGER,
            monthly_unit_price_snapshot REAL NOT NULL CHECK(monthly_unit_price_snapshot >= 0),
            currency TEXT NOT NULL DEFAULT 'UAH',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(request_id, access_point_code)
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_phone_access_request_points_request
        ON phone_access_request_points(request_id, access_point_code)
        """
    )


def ensure_phone_access_operational_schema(
    conn: sqlite3.Connection,
    *,
    actor_id: str = "schema_migration",
    sandbox_db_path: str = "",
) -> list[str]:
    """
    Add the durable request-to-order bridge.

    The caller owns the transaction. This function never modifies existing
    service orders, payments, remotes or legacy generic phone credentials.
    """
    cur = conn.cursor()
    before_v1 = _created_tables(cur)
    changes: list[str] = []
    if not required_phone_access_tables().issubset(before_v1):
        changes.extend(
            ensure_phone_barrier_access_schema(
                conn,
                effective_from=date_db(),
                actor_id=actor_id,
                sandbox_db_path=sandbox_db_path,
            )
        )
    cur = conn.cursor()
    before = _created_tables(cur)
    _insert_operational_schema(cur)
    after = _created_tables(cur)
    for table in sorted(_operational_tables() - before):
        changes.append(f"created table {table}")

    prior = _fetchone_dict(
        cur,
        "SELECT migration_code FROM access_schema_migrations WHERE migration_code = ?",
        (OPERATIONAL_SCHEMA_MIGRATION_CODE,),
    )
    if not prior:
        cur.execute(
            """
            INSERT INTO access_schema_migrations (
                migration_code, schema_version, applied_at, applied_by,
                sandbox_db_path, note
            )
            VALUES (?, '2.0', ?, ?, ?, ?)
            """,
            (
                OPERATIONAL_SCHEMA_MIGRATION_CODE,
                now_db(),
                actor_id,
                sandbox_db_path,
                "Operational request bridge for two-barrier phone access.",
            ),
        )
        changes.append(f"recorded migration {OPERATIONAL_SCHEMA_MIGRATION_CODE}")
        _record_change(
            cur,
            event_number=f"MIG-{OPERATIONAL_SCHEMA_MIGRATION_CODE}",
            event_type="SCHEMA_MIGRATION_APPLIED",
            source_context="phone_barrier_access_operational_schema",
            payload={
                "migration_code": OPERATIONAL_SCHEMA_MIGRATION_CODE,
                "created_tables": sorted(_operational_tables() - before),
            },
            actor_id=actor_id,
        )
    return changes or ["operational phone-access schema already present"]


def _request_row(
    cur: sqlite3.Cursor,
    *,
    interest_id: int | None = None,
    service_order_id: int | None = None,
) -> dict | None:
    if interest_id is not None:
        return _fetchone_dict(
            cur, "SELECT * FROM phone_access_requests WHERE interest_id = ?", (int(interest_id),)
        )
    if service_order_id is not None:
        return _fetchone_dict(
            cur,
            "SELECT * FROM phone_access_requests WHERE service_order_id = ?",
            (int(service_order_id),),
        )
    raise ValueError("Нужно указать interest_id или service_order_id.")


def _request_points(cur: sqlite3.Cursor, request_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT *
        FROM phone_access_request_points
        WHERE request_id = ?
        ORDER BY access_point_code, id
        """,
        (int(request_id),),
    )
    return [dict(row) for row in cur.fetchall()]


def create_phone_access_request_from_interest(
    *,
    interest: dict,
    phone: str,
    quote: dict,
    parking_debt_check_mode: str = "MANUAL_REVIEW",
    parking_debt_check_note: str = "",
    conn: sqlite3.Connection,
) -> dict:
    """Persist phone and selected barriers next to an already-created interest."""
    ensure_phone_access_operational_schema(conn)
    phone = normalise_phone_number(phone)
    mode = text(parking_debt_check_mode).upper() or "MANUAL_REVIEW"
    allowed = {
        "CHECK_LINKED_PARKING_ACCOUNT",
        "NOT_APPLICABLE_NO_PARKING",
        "MANUAL_REVIEW",
    }
    if mode not in allowed:
        raise ValueError("Некорректный режим проверки парковочной задолженности.")

    cur = conn.cursor()
    interest_id = int(interest["id"])
    existing = _request_row(cur, interest_id=interest_id)
    if existing:
        return phone_access_request_summary(interest_id=interest_id, conn=conn)

    points = list(quote.get("access_points") or [])
    if not points:
        raise ValueError("В намерении не выбраны шлагбаумы.")

    request_number = _next_number("PAR", cur, "phone_access_requests", "request_number")
    now = now_db()
    cur.execute(
        """
        INSERT INTO phone_access_requests (
            request_number, interest_id, resident_account_id, telegram_user_id,
            apartment_id, apartment_number, phone_normalized, request_status,
            parking_debt_check_mode, parking_debt_check_note, policy_version_id,
            quoted_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_number,
            interest_id,
            interest.get("resident_account_id"),
            text(interest.get("telegram_user_id")) or None,
            interest.get("apartment_id"),
            text(interest.get("apartment_number")),
            phone,
            REQUEST_INTEREST,
            mode,
            text(parking_debt_check_note) or None,
            int(quote["policy_version_id"]),
            now,
            now,
            now,
        ),
    )
    request_id = int(cur.lastrowid)
    for point in points:
        cur.execute(
            """
            INSERT INTO phone_access_request_points (
                request_id, access_point_code, access_point_name_snapshot,
                connect_tariff_version_id, connect_unit_price_snapshot,
                monthly_tariff_version_id, monthly_unit_price_snapshot,
                currency, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                point["access_point_code"],
                point.get("access_point_name_uk") or point["access_point_code"],
                point.get("connect_tariff_version_id"),
                float(point["connect_unit_price"]),
                point.get("monthly_tariff_version_id"),
                float(point["monthly_unit_price"]),
                point.get("currency") or quote.get("currency") or "UAH",
                now,
                now,
            ),
        )

    _record_change(
        cur,
        event_number=_next_number("AOP", cur, "access_operation_journal", "event_number"),
        event_type="PHONE_ACCESS_INTEREST_CREATED",
        source_context="phone_access_request",
        payload={
            "request_number": request_number,
            "interest_id": interest_id,
            "phone": phone,
            "access_points": [row["access_point_code"] for row in points],
            "connection_total": quote["connection_total"],
            "monthly_total": quote["monthly_total"],
        },
        actor_id=text(interest.get("telegram_user_id")) or "resident",
    )
    return phone_access_request_summary(interest_id=interest_id, conn=conn)


def _subscription_row(
    cur: sqlite3.Cursor,
    *,
    subscription_id: int | None = None,
    order_id: int | None = None,
    interest_id: int | None = None,
) -> dict | None:
    if subscription_id is not None:
        return _fetchone_dict(
            cur, "SELECT * FROM phone_access_subscriptions WHERE id = ?", (int(subscription_id),)
        )
    if order_id is not None:
        return _fetchone_dict(
            cur,
            "SELECT * FROM phone_access_subscriptions WHERE created_from_order_id = ? ORDER BY id DESC LIMIT 1",
            (int(order_id),),
        )
    if interest_id is not None:
        return _fetchone_dict(
            cur,
            "SELECT * FROM phone_access_subscriptions WHERE created_from_interest_id = ? ORDER BY id DESC LIMIT 1",
            (int(interest_id),),
        )
    raise ValueError("Нужно указать subscription_id, order_id или interest_id.")


def _subscription_points(cur: sqlite3.Cursor, subscription_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT *
        FROM phone_access_subscription_points
        WHERE subscription_id = ?
        ORDER BY access_point_code, id
        """,
        (int(subscription_id),),
    )
    return [dict(row) for row in cur.fetchall()]


def _subscription_charges(cur: sqlite3.Cursor, subscription_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT *
        FROM phone_access_subscription_charges
        WHERE subscription_id = ?
        ORDER BY charge_kind, billing_period, access_point_code, id
        """,
        (int(subscription_id),),
    )
    return [dict(row) for row in cur.fetchall()]


def phone_access_request_summary(
    *,
    interest_id: int | None = None,
    order_id: int | None = None,
    conn: sqlite3.Connection,
) -> dict | None:
    cur = conn.cursor()
    request = _request_row(cur, interest_id=interest_id, service_order_id=order_id)
    if not request:
        return None
    points = _request_points(cur, int(request["id"]))
    subscription = _subscription_row(
        cur,
        order_id=request.get("service_order_id"),
        interest_id=request.get("interest_id"),
    )
    result = dict(request)
    result["points"] = points
    result["connection_total"] = round(
        sum(float(row.get("connect_unit_price_snapshot") or 0) for row in points), 2
    )
    result["monthly_total"] = round(
        sum(float(row.get("monthly_unit_price_snapshot") or 0) for row in points), 2
    )
    result["currency"] = text(points[0].get("currency")) if points else "UAH"
    if subscription:
        result["subscription"] = subscription
        result["subscription_points"] = _subscription_points(cur, int(subscription["id"]))
        result["charges"] = _subscription_charges(cur, int(subscription["id"]))
    else:
        result["subscription"] = None
        result["subscription_points"] = []
        result["charges"] = []
    return result


def _subscription_summary(
    cur: sqlite3.Cursor,
    subscription: dict,
) -> dict:
    result = dict(subscription)
    result["points"] = _subscription_points(cur, int(subscription["id"]))
    result["charges"] = _subscription_charges(cur, int(subscription["id"]))
    result["connection_total"] = round(
        sum(
            float(row.get("amount_due_snapshot") or 0)
            for row in result["charges"]
            if text(row.get("charge_kind")) == CHARGE_CONNECT
        ),
        2,
    )
    result["monthly_total"] = round(
        sum(
            float(row.get("unit_price_snapshot") or 0)
            for row in result["charges"]
            if text(row.get("charge_kind")) == CHARGE_MONTHLY
            and text(row.get("billing_period")) == text(subscription.get("first_charge_period"))
        ),
        2,
    )
    return result


def phone_access_subscription_summary_for_order(
    *,
    order_id: int,
    conn: sqlite3.Connection,
) -> dict | None:
    cur = conn.cursor()
    subscription = _subscription_row(cur, order_id=order_id)
    return _subscription_summary(cur, subscription) if subscription else None


def _insert_subscription_connect_charges(
    cur: sqlite3.Cursor,
    *,
    subscription: dict,
    request_points: list[dict],
    service_order_id: int,
    payment_id: int,
    paid_at: str,
) -> None:
    for point in request_points:
        point_row = _fetchone_dict(
            cur,
            """
            SELECT id
            FROM phone_access_subscription_points
            WHERE subscription_id = ? AND access_point_code = ?
            """,
            (int(subscription["id"]), point["access_point_code"]),
        )
        if not point_row:
            raise RuntimeError("Не создано право доступа для выбранного шлагбаума.")
        key = f"CONNECT:{subscription['subscription_number']}"
        existing = _fetchone_dict(
            cur,
            """
            SELECT id
            FROM phone_access_subscription_charges
            WHERE subscription_point_id = ? AND charge_kind = ? AND charge_period_key = ?
            """,
            (int(point_row["id"]), CHARGE_CONNECT, key),
        )
        if existing:
            continue
        cur.execute(
            """
            INSERT INTO phone_access_subscription_charges (
                charge_number, subscription_id, subscription_point_id, access_point_code,
                charge_kind, charge_period_key, billing_period, tariff_code,
                tariff_version_id, quantity, unit_price_snapshot,
                amount_due_snapshot, currency, due_date, charge_status,
                service_order_id, payment_id, posted_at, paid_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _next_number("PAC", cur, "phone_access_subscription_charges", "charge_number"),
                int(subscription["id"]),
                int(point_row["id"]),
                point["access_point_code"],
                CHARGE_CONNECT,
                key,
                TARIFF_CONNECT,
                point.get("connect_tariff_version_id"),
                float(point["connect_unit_price_snapshot"]),
                float(point["connect_unit_price_snapshot"]),
                point.get("currency") or "UAH",
                paid_at[:10],
                CHARGE_PAID,
                int(service_order_id),
                int(payment_id),
                paid_at,
                paid_at,
                now_db(),
                now_db(),
            ),
        )


def promote_paid_phone_access_request(
    *,
    interest: dict,
    order: dict,
    payment_id: int,
    conn: sqlite3.Connection,
    registered_at: str | date | datetime | None = None,
) -> dict | None:
    """
    Create the subscription only after the matching payment has created the
    real service order. Non-phone interests return None unchanged.
    """
    ensure_phone_access_operational_schema(conn)
    cur = conn.cursor()
    request = _request_row(cur, interest_id=int(interest["id"]))
    if not request:
        return None

    order_id = int(order["id"])
    existing = _subscription_row(cur, order_id=order_id)
    if existing:
        return _subscription_summary(cur, existing)

    if request.get("service_order_id") not in (None, order_id):
        raise RuntimeError("Телефонный запрос уже связан с другим заказом.")
    registered = parse_iso_date(registered_at).isoformat()
    request_points = _request_points(cur, int(request["id"]))
    if not request_points:
        raise RuntimeError("В телефонном запросе отсутствуют выбранные шлагбаумы.")

    policy, values = _active_policy(cur, as_of=registered)
    rule = values.get(POLICY_MONTHLY_START_RULE, "")
    if rule != MONTHLY_START_RULE:
        raise RuntimeError("Неизвестное правило первой абонплаты: " + (rule or "—"))
    first_period = first_charge_period(registered)

    subscription_number = _next_number(
        "PAS", cur, "phone_access_subscriptions", "subscription_number"
    )
    now = now_db()
    cur.execute(
        """
        INSERT INTO phone_access_subscriptions (
            subscription_number, resident_account_id, apartment_id, apartment_number,
            holder_type, phone_normalized, subscription_status,
            parking_debt_check_mode, parking_debt_check_note,
            activation_date, first_charge_period, monthly_start_policy_version_id,
            created_from_order_id, created_from_interest_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'RESIDENT', ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (
            subscription_number,
            request.get("resident_account_id"),
            request.get("apartment_id"),
            text(request.get("apartment_number")),
            text(request["phone_normalized"]),
            ACCESS_STATUS_PENDING,
            text(request.get("parking_debt_check_mode")) or "MANUAL_REVIEW",
            request.get("parking_debt_check_note"),
            first_period,
            int(policy["id"]),
            order_id,
            int(interest["id"]),
            now,
            now,
        ),
    )
    subscription_id = int(cur.lastrowid)

    for point in request_points:
        cur.execute(
            """
            INSERT INTO phone_access_subscription_points (
                subscription_id, access_point_code, access_point_name_snapshot,
                point_status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                subscription_id,
                point["access_point_code"],
                point["access_point_name_snapshot"],
                POINT_PENDING_ACTIVATION,
                now,
                now,
            ),
        )

    cur.execute(
        """
        UPDATE phone_access_requests
        SET service_order_id = ?, request_status = ?, registered_at = ?,
            first_charge_period = ?, paid_at = ?, policy_version_id = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            order_id,
            REQUEST_PAID_ORDER_CREATED,
            registered,
            first_period,
            now,
            int(policy["id"]),
            now,
            int(request["id"]),
        ),
    )

    subscription = _subscription_row(cur, subscription_id=subscription_id)
    _insert_subscription_connect_charges(
        cur,
        subscription=subscription,
        request_points=request_points,
        service_order_id=order_id,
        payment_id=int(payment_id),
        paid_at=now,
    )

    _record_change(
        cur,
        event_number=_next_number("AOP", cur, "access_operation_journal", "event_number"),
        event_type="PHONE_ACCESS_SUBSCRIPTION_CREATED",
        source_context="paid_phone_access_interest",
        payload={
            "subscription_number": subscription_number,
            "request_number": request["request_number"],
            "order_id": order_id,
            "payment_id": int(payment_id),
            "first_charge_period": first_period,
            "access_points": [row["access_point_code"] for row in request_points],
        },
        actor_id="system",
    )
    return _subscription_summary(cur, subscription)


def ensure_monthly_phone_access_charges(
    *,
    subscription_id: int,
    as_of: str | date | datetime | None = None,
    conn: sqlite3.Connection,
) -> list[dict]:
    """
    Open monthly charges only for active access rights.

    The V2 module does not invent a collection deadline: generated charges are
    OPEN, and debt enforcement is deliberately implemented in a later module
    once the payment/parking debt sources are connected.
    """
    cur = conn.cursor()
    subscription = _subscription_row(cur, subscription_id=int(subscription_id))
    if not subscription:
        raise ValueError("Подписка не найдена.")
    if text(subscription.get("subscription_status")) not in {
        ACCESS_STATUS_ACTIVE,
        ACCESS_STATUS_PARTIALLY_ACTIVE,
    }:
        return []

    first_period = text(subscription.get("first_charge_period"))
    if not re.fullmatch(r"\d{4}-\d{2}", first_period):
        return []
    as_of_date = parse_iso_date(as_of)
    target_period = f"{as_of_date.year:04d}-{as_of_date.month:02d}"
    if first_period > target_period:
        return []

    # Generate only through the current requested period, month by month.
    year, month = [int(part) for part in first_period.split("-")]
    to_year, to_month = as_of_date.year, as_of_date.month
    created: list[dict] = []
    points = [
        row for row in _subscription_points(cur, int(subscription["id"]))
        if text(row.get("point_status")) == POINT_ACTIVE
    ]
    while (year, month) <= (to_year, to_month):
        period = f"{year:04d}-{month:02d}"
        period_date = f"{period}-01"
        for point in points:
            existing = _fetchone_dict(
                cur,
                """
                SELECT *
                FROM phone_access_subscription_charges
                WHERE subscription_point_id = ?
                  AND charge_kind = ?
                  AND charge_period_key = ?
                """,
                (int(point["id"]), CHARGE_MONTHLY, period),
            )
            if existing:
                continue
            tariff = resolve_tariff(
                tariff_code=TARIFF_MONTHLY,
                access_point_code=text(point["access_point_code"]),
                as_of=period_date,
                conn=conn,
            )
            now = now_db()
            cur.execute(
                """
                INSERT INTO phone_access_subscription_charges (
                    charge_number, subscription_id, subscription_point_id,
                    access_point_code, charge_kind, charge_period_key, billing_period,
                    tariff_code, tariff_version_id, quantity,
                    unit_price_snapshot, amount_due_snapshot, currency,
                    charge_status, posted_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _next_number("PAC", cur, "phone_access_subscription_charges", "charge_number"),
                    int(subscription["id"]),
                    int(point["id"]),
                    text(point["access_point_code"]),
                    CHARGE_MONTHLY,
                    period,
                    period,
                    TARIFF_MONTHLY,
                    int(tariff["id"]),
                    float(tariff["amount"]),
                    float(tariff["amount"]),
                    text(tariff.get("currency")) or "UAH",
                    CHARGE_OPEN,
                    now,
                    now,
                    now,
                ),
            )
            created.append(
                _fetchone_dict(
                    cur,
                    "SELECT * FROM phone_access_subscription_charges WHERE id = ?",
                    (int(cur.lastrowid),),
                )
            )
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return created


def activate_phone_access_subscription_for_order(
    *,
    order_id: int,
    actor_id: int | str | None,
    conn: sqlite3.Connection,
) -> dict:
    """
    Sandbox accounting activation for every selected barrier.

    It writes one credential row per barrier and confirms the existing
    DIGITAL_ACCESS_ACTIVATED service-order step only after all selected
    rights are active. It never sends a real controller command.
    """
    ensure_phone_access_operational_schema(conn)
    from access_control import has_permission
    from service_orders_core import confirm_order_step, get_service_order

    if not has_permission(
        actor_id,
        "service_access_credentials",
        "ACTIVATE",
        scope_type="SERVICE_CATEGORY",
        scope_value="ACCESS",
    ):
        raise PermissionError("Нет права активировать телефонный доступ.")

    cur = conn.cursor()
    order = get_service_order(cur, int(order_id))
    subscription = _subscription_row(cur, order_id=int(order_id))
    if not subscription:
        raise ValueError(
            "Для этой заявки нет новой подписки телефонного доступа. "
            "Для исторической заявки используйте совместимый старый сценарий."
        )

    points = _subscription_points(cur, int(subscription["id"]))
    if not points:
        raise RuntimeError("У подписки нет выбранных шлагбаумов.")
    now = now_db()
    any_active = False
    for point in points:
        status = text(point.get("point_status"))
        if status == POINT_ACTIVE:
            any_active = True
            continue
        if status not in {POINT_PENDING_ACTIVATION, "EXTERNAL_SYNC_ERROR"}:
            raise ValueError(
                "Нельзя активировать шлагбаум в текущем состоянии: "
                + text(point.get("access_point_code"))
                + " / "
                + status
            )

        existing_credential = _fetchone_dict(
            cur,
            """
            SELECT id
            FROM service_access_credentials
            WHERE service_order_id = ?
              AND credential_kind = 'PHONE'
              AND credential_value = ?
              AND access_scope = ?
              AND credential_status = 'ACTIVE'
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                int(order_id),
                text(subscription["phone_normalized"]),
                text(point["access_point_code"]),
            ),
        )
        if not existing_credential:
            cur.execute(
                """
                INSERT INTO service_access_credentials (
                    service_order_id, credential_kind, credential_value,
                    access_scope, credential_status, external_reference,
                    apartment_id, apartment_number,
                    activated_by, activated_at, note, created_at, updated_at
                )
                VALUES (?, 'PHONE', ?, ?, 'ACTIVE', NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(order_id),
                    text(subscription["phone_normalized"]),
                    text(point["access_point_code"]),
                    order.get("apartment_id"),
                    text(order.get("apartment_number")),
                    str(actor_id) if actor_id is not None else "system",
                    now,
                    (
                        "Sandbox accounting activation only; real controller "
                        "integration is not configured."
                    ),
                    now,
                    now,
                ),
            )

        cur.execute(
            """
            UPDATE phone_access_subscription_points
            SET point_status = ?,
                activated_at = COALESCE(activated_at, ?),
                last_external_sync_status = ?,
                last_external_sync_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                POINT_ACTIVE,
                now,
                "SANDBOX_ACCOUNTING_ACTIVATED",
                now,
                now,
                int(point["id"]),
            ),
        )
        _record_change(
            cur,
            event_number=_next_number("AOP", cur, "access_operation_journal", "event_number"),
            event_type="PHONE_ACCESS_POINT_ACTIVATED",
            source_context="sandbox_accounting_activation",
            payload={
                "order_id": int(order_id),
                "subscription_id": int(subscription["id"]),
                "access_point_code": point["access_point_code"],
                "phone": subscription["phone_normalized"],
            },
            actor_id=str(actor_id) if actor_id is not None else "system",
        )
        any_active = True

    points = _subscription_points(cur, int(subscription["id"]))
    active_count = sum(
        1 for point in points if text(point.get("point_status")) == POINT_ACTIVE
    )
    if active_count != len(points):
        status = ACCESS_STATUS_PARTIALLY_ACTIVE if active_count else ACCESS_STATUS_PENDING
        cur.execute(
            """
            UPDATE phone_access_subscriptions
            SET subscription_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now_db(), int(subscription["id"])),
        )
        raise RuntimeError("Активированы не все выбранные шлагбаумы.")

    cur.execute(
        """
        UPDATE phone_access_subscriptions
        SET subscription_status = ?, activation_date = COALESCE(activation_date, ?),
            updated_at = ?
        WHERE id = ?
        """,
        (ACCESS_STATUS_ACTIVE, now[:10], now, int(subscription["id"])),
    )
    cur.execute(
        """
        UPDATE phone_access_requests
        SET request_status = ?, updated_at = ?
        WHERE service_order_id = ?
        """,
        (REQUEST_ACTIVE, now, int(order_id)),
    )

    order_after = confirm_order_step(
        order_id=int(order_id),
        step_code="DIGITAL_ACCESS_ACTIVATED",
        actor_id=actor_id,
        note=(
            "В sandbox учётно активированы все выбранные шлагбаумы: "
            + ", ".join(text(row["access_point_code"]) for row in points)
            + ". Реальные команды контроллерам не отправлялись."
        ),
        source_context="phone_barrier_access",
        conn=conn,
    )
    ensure_monthly_phone_access_charges(
        subscription_id=int(subscription["id"]),
        as_of=now[:10],
        conn=conn,
    )
    subscription_after = _subscription_row(cur, subscription_id=int(subscription["id"]))
    return {
        "order": order_after,
        "subscription": _subscription_summary(cur, subscription_after),
    }
