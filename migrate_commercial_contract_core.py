"""
Миграция договорного ядра коммерческих помещений (КП).

Создаёт отдельную договорную логику для коммерческих помещений:
- индивидуальные договоры и условия;
- представители договора с Telegram ID для уведомлений;
- номера телефонного доступа к шлагбауму;
- очередь Telegram-уведомлений о долге;
- ручные действия оператора по доступу.

ВАЖНО
------
1. Цена для КП хранится в строке конкретного договора, а не в общем тарифе.
2. SMS должникам не используется. Уведомления должникам — Telegram.
3. Никаких SMS-команд шлагбауму и никакого GSM-управления этот код не делает.
4. Отключение/восстановление доступа пока фиксируется только как ручное
   действие оператора с аудитом.
5. Общие charges / payments не дублируются:
   к существующим charges добавляются contract-link поля, а оплата
   распределяется обычными payment_allocations.

Запуск:
  g:/Programming/Py/venv/Scripts/python.exe G:/Programming/Py/OSBB/migrate_commercial_contract_core.py
  g:/Programming/Py/venv/Scripts/python.exe G:/Programming/Py/OSBB/migrate_commercial_contract_core.py --apply
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
for folder in (ROOT, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB

try:
    from audit_logger import audit_log
except Exception:
    audit_log = None


GENERIC_COMMERCIAL_SERVICE_CODE = "COMMERCIAL_CONTRACT"
GENERIC_COMMERCIAL_SERVICE_NAME = "Индивидуальная услуга коммерческого договора"


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def object_exists(cur: sqlite3.Cursor, object_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE name=?",
        (object_name,),
    )
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, table_name: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({q(table_name)})")
    return {row[1] for row in cur.fetchall()}


def add_column_if_missing(
    cur: sqlite3.Cursor,
    table_name: str,
    column_name: str,
    definition: str,
) -> bool:
    if column_name in table_columns(cur, table_name):
        return False
    cur.execute(
        f"ALTER TABLE {q(table_name)} ADD COLUMN {q(column_name)} {definition}"
    )
    return True


def index_exists(cur: sqlite3.Cursor, index_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    )
    return cur.fetchone() is not None


def create_index_if_missing(
    cur: sqlite3.Cursor,
    index_name: str,
    sql: str,
) -> bool:
    if index_exists(cur, index_name):
        return False
    cur.execute(sql)
    return True


def required_fields_with_defaults(
    cur: sqlite3.Cursor,
    table_name: str,
) -> dict[str, Any]:
    """
    Возвращает минимальные заполнители для непустых полей старой схемы.
    Это нужно только для безопасного upsert service_catalog в БД,
    где каталог уже менялся несколькими миграциями.
    """
    cur.execute(f"PRAGMA table_info({q(table_name)})")
    result: dict[str, Any] = {}

    for row in cur.fetchall():
        _cid, name, type_name, notnull, default_value, primary_key = row[:6]
        if primary_key or not int(notnull or 0) or default_value is not None:
            continue

        upper_name = str(name).upper()
        upper_type = str(type_name or "").upper()

        if name in {"service_code", "code"}:
            result[name] = GENERIC_COMMERCIAL_SERVICE_CODE
        elif name in {"service_name", "name", "title"}:
            result[name] = GENERIC_COMMERCIAL_SERVICE_NAME
        elif name == "service_group":
            result[name] = "COMMERCIAL"
        elif name == "service_type":
            result[name] = "CONTRACT"
        elif name == "category":
            result[name] = "COMMERCIAL"
        elif name == "unit":
            result[name] = "contract"
        elif "ACTIVE" in upper_name:
            result[name] = 1
        elif "DATE" in upper_name or "TIME" in upper_name or upper_name.endswith("_AT"):
            result[name] = now_db()
        elif "INT" in upper_type:
            result[name] = 0
        elif any(token in upper_type for token in ("REAL", "NUM", "DEC", "FLOAT")):
            result[name] = 0
        else:
            result[name] = ""

    return result


def seed_generic_commercial_service(
    cur: sqlite3.Cursor,
) -> tuple[bool, str]:
    """
    Catalog code — только классификатор. Он НЕ задаёт цену для КП.
    """
    if not table_exists(cur, "service_catalog"):
        return False, "service_catalog отсутствует — пропущено"

    columns = table_columns(cur, "service_catalog")
    values: dict[str, Any] = required_fields_with_defaults(cur, "service_catalog")

    candidates = {
        "service_code": GENERIC_COMMERCIAL_SERVICE_CODE,
        "service_name": GENERIC_COMMERCIAL_SERVICE_NAME,
        "service_group": "COMMERCIAL",
        "service_type": "CONTRACT",
        "category": "COMMERCIAL",
        "unit": "contract",
        "is_active": 1,
        "is_monthly": 0,
        "is_fundraising": 0,
        "is_commercial": 1,
        "is_access_control": 0,
        "is_cash_collectable": 1,
        "comment": (
            "Классификатор начислений по индивидуальным договорам КП. "
            "Цена определяется строкой договора, не service_tariffs."
        ),
        "created_at": now_db(),
        "updated_at": now_db(),
    }
    values.update({key: value for key, value in candidates.items() if key in columns})

    if "service_code" not in columns:
        return False, "service_catalog не содержит service_code — пропущено"

    cur.execute(
        "SELECT id FROM service_catalog WHERE service_code=?",
        (GENERIC_COMMERCIAL_SERVICE_CODE,),
    )
    existing = cur.fetchone()

    if existing:
        update_values = {
            key: value
            for key, value in values.items()
            if key in columns and key not in {"service_code", "created_at"}
        }
        if update_values:
            cur.execute(
                f"UPDATE service_catalog SET "
                f"{', '.join(f'{q(key)}=?' for key in update_values)} "
                f"WHERE service_code=?",
                tuple(update_values.values()) + (GENERIC_COMMERCIAL_SERVICE_CODE,),
            )
        return False, "обновлён"

    insert_columns = [key for key in values if key in columns]
    cur.execute(
        f"INSERT INTO service_catalog ({', '.join(q(key) for key in insert_columns)}) "
        f"VALUES ({','.join('?' for _ in insert_columns)})",
        tuple(values[key] for key in insert_columns),
    )
    return True, "создан"


def ensure_core_tables(cur: sqlite3.Cursor) -> list[str]:
    created: list[str] = []

    definitions: list[tuple[str, str]] = [
        (
            "commercial_contracts",
            """
            CREATE TABLE IF NOT EXISTS commercial_contracts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unit_id INTEGER NOT NULL,
                contract_number TEXT,
                counterparty_type TEXT NOT NULL DEFAULT 'UNKNOWN',
                counterparty_name TEXT,
                status TEXT NOT NULL DEFAULT 'DRAFT',
                valid_from TEXT,
                valid_to TEXT,
                payment_due_day INTEGER NOT NULL DEFAULT 10,
                grace_days INTEGER NOT NULL DEFAULT 0,
                reminder_days_before_due INTEGER NOT NULL DEFAULT 3,
                warning_days_overdue INTEGER NOT NULL DEFAULT 3,
                suspension_candidate_days_overdue INTEGER NOT NULL DEFAULT 7,
                internal_note TEXT,
                created_by TEXT,
                updated_by TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY(unit_id) REFERENCES apartments(id)
            )
            """,
        ),
        (
            "commercial_contract_items",
            """
            CREATE TABLE IF NOT EXISTS commercial_contract_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id INTEGER NOT NULL,
                item_code TEXT,
                item_name TEXT NOT NULL,
                reference_service_code TEXT,
                calculation_mode TEXT NOT NULL DEFAULT 'FIXED_MONTHLY',
                fixed_amount REAL,
                rate_amount REAL,
                quantity_default REAL NOT NULL DEFAULT 1,
                currency TEXT NOT NULL DEFAULT 'UAH',
                blocks_phone_access_on_debt INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                valid_from TEXT,
                valid_to TEXT,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY(contract_id) REFERENCES commercial_contracts(id)
            )
            """,
        ),
        (
            "commercial_contract_recipients",
            """
            CREATE TABLE IF NOT EXISTS commercial_contract_recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id INTEGER NOT NULL,
                resident_account_id INTEGER,
                telegram_user_id TEXT,
                recipient_name TEXT,
                recipient_role TEXT NOT NULL DEFAULT 'REPRESENTATIVE',
                is_primary INTEGER NOT NULL DEFAULT 0,
                notification_enabled INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY(contract_id) REFERENCES commercial_contracts(id),
                FOREIGN KEY(resident_account_id) REFERENCES resident_accounts(id)
            )
            """,
        ),
        (
            "commercial_access_phones",
            """
            CREATE TABLE IF NOT EXISTS commercial_access_phones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id INTEGER NOT NULL,
                phone_normalized TEXT NOT NULL,
                phone_display TEXT,
                access_purpose TEXT NOT NULL DEFAULT 'STAFF',
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                status_reason TEXT,
                status_changed_at TEXT,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY(contract_id) REFERENCES commercial_contracts(id)
            )
            """,
        ),
        (
            "commercial_notifications",
            """
            CREATE TABLE IF NOT EXISTS commercial_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id INTEGER NOT NULL,
                recipient_id INTEGER,
                telegram_user_id TEXT,
                notification_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'DRAFT',
                dedupe_key TEXT NOT NULL,
                charge_ids_json TEXT,
                debt_amount_snapshot REAL NOT NULL DEFAULT 0,
                days_overdue_snapshot INTEGER NOT NULL DEFAULT 0,
                message_text TEXT NOT NULL,
                created_by TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT,
                failed_at TEXT,
                delivery_error TEXT,
                cancelled_at TEXT,
                cancelled_by TEXT,
                FOREIGN KEY(contract_id) REFERENCES commercial_contracts(id),
                FOREIGN KEY(recipient_id) REFERENCES commercial_contract_recipients(id)
            )
            """,
        ),
        (
            "commercial_access_actions",
            """
            CREATE TABLE IF NOT EXISTS commercial_access_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id INTEGER NOT NULL,
                access_phone_id INTEGER,
                notification_id INTEGER,
                action_type TEXT NOT NULL,
                action_status TEXT NOT NULL DEFAULT 'OPEN',
                debt_amount_snapshot REAL NOT NULL DEFAULT 0,
                days_overdue_snapshot INTEGER NOT NULL DEFAULT 0,
                reason TEXT,
                operator_id TEXT,
                performed_at TEXT,
                comment TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY(contract_id) REFERENCES commercial_contracts(id),
                FOREIGN KEY(access_phone_id) REFERENCES commercial_access_phones(id),
                FOREIGN KEY(notification_id) REFERENCES commercial_notifications(id)
            )
            """,
        ),
    ]

    for table_name, sql in definitions:
        exists_before = table_exists(cur, table_name)
        cur.execute(sql)
        if not exists_before:
            created.append(table_name)

    return created


def ensure_core_columns(cur: sqlite3.Cursor) -> dict[str, list[str]]:
    added: dict[str, list[str]] = {}

    column_sets = {
        "commercial_contracts": {
            "contract_number": "TEXT",
            "counterparty_type": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "counterparty_name": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'DRAFT'",
            "valid_from": "TEXT",
            "valid_to": "TEXT",
            "payment_due_day": "INTEGER NOT NULL DEFAULT 10",
            "grace_days": "INTEGER NOT NULL DEFAULT 0",
            "reminder_days_before_due": "INTEGER NOT NULL DEFAULT 3",
            "warning_days_overdue": "INTEGER NOT NULL DEFAULT 3",
            "suspension_candidate_days_overdue": "INTEGER NOT NULL DEFAULT 7",
            "internal_note": "TEXT",
            "created_by": "TEXT",
            "updated_by": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        },
        "commercial_contract_items": {
            "item_code": "TEXT",
            "reference_service_code": "TEXT",
            "calculation_mode": "TEXT NOT NULL DEFAULT 'FIXED_MONTHLY'",
            "fixed_amount": "REAL",
            "rate_amount": "REAL",
            "quantity_default": "REAL NOT NULL DEFAULT 1",
            "currency": "TEXT NOT NULL DEFAULT 'UAH'",
            "blocks_phone_access_on_debt": "INTEGER NOT NULL DEFAULT 0",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
            "valid_from": "TEXT",
            "valid_to": "TEXT",
            "note": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        },
        "commercial_contract_recipients": {
            "resident_account_id": "INTEGER",
            "telegram_user_id": "TEXT",
            "recipient_name": "TEXT",
            "recipient_role": "TEXT NOT NULL DEFAULT 'REPRESENTATIVE'",
            "is_primary": "INTEGER NOT NULL DEFAULT 0",
            "notification_enabled": "INTEGER NOT NULL DEFAULT 1",
            "status": "TEXT NOT NULL DEFAULT 'ACTIVE'",
            "note": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        },
        "commercial_access_phones": {
            "phone_display": "TEXT",
            "access_purpose": "TEXT NOT NULL DEFAULT 'STAFF'",
            "status": "TEXT NOT NULL DEFAULT 'ACTIVE'",
            "status_reason": "TEXT",
            "status_changed_at": "TEXT",
            "note": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        },
        "commercial_notifications": {
            "recipient_id": "INTEGER",
            "telegram_user_id": "TEXT",
            "notification_type": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'DRAFT'",
            "dedupe_key": "TEXT",
            "charge_ids_json": "TEXT",
            "debt_amount_snapshot": "REAL NOT NULL DEFAULT 0",
            "days_overdue_snapshot": "INTEGER NOT NULL DEFAULT 0",
            "message_text": "TEXT",
            "created_by": "TEXT",
            "created_at": "TEXT",
            "sent_at": "TEXT",
            "failed_at": "TEXT",
            "delivery_error": "TEXT",
            "cancelled_at": "TEXT",
            "cancelled_by": "TEXT",
        },
        "commercial_access_actions": {
            "access_phone_id": "INTEGER",
            "notification_id": "INTEGER",
            "action_type": "TEXT",
            "action_status": "TEXT NOT NULL DEFAULT 'OPEN'",
            "debt_amount_snapshot": "REAL NOT NULL DEFAULT 0",
            "days_overdue_snapshot": "INTEGER NOT NULL DEFAULT 0",
            "reason": "TEXT",
            "operator_id": "TEXT",
            "performed_at": "TEXT",
            "comment": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        },
    }

    for table_name, definitions in column_sets.items():
        added[table_name] = []
        for name, definition in definitions.items():
            if add_column_if_missing(cur, table_name, name, definition):
                added[table_name].append(name)

    return added


def ensure_billing_links(cur: sqlite3.Cursor) -> dict[str, list[str]]:
    """
    Общий billing ledger не дублируем. Только добавляем связи:
    charge -> договор КП -> строка договора -> срок оплаты.
    """
    added: dict[str, list[str]] = {"charges": [], "payments": [], "cashbox_operations": []}

    if table_exists(cur, "charges"):
        for name, definition in {
            "commercial_contract_id": "INTEGER",
            "commercial_contract_item_id": "INTEGER",
            "commercial_due_date": "TEXT",
        }.items():
            if add_column_if_missing(cur, "charges", name, definition):
                added["charges"].append(name)

    if table_exists(cur, "payments"):
        for name, definition in {
            "commercial_contract_id": "INTEGER",
            "commercial_unit_id": "INTEGER",
        }.items():
            if add_column_if_missing(cur, "payments", name, definition):
                added["payments"].append(name)

    if table_exists(cur, "cashbox_operations"):
        for name, definition in {
            "commercial_contract_id": "INTEGER",
            "commercial_unit_id": "INTEGER",
        }.items():
            if add_column_if_missing(cur, "cashbox_operations", name, definition):
                added["cashbox_operations"].append(name)

    return added


def ensure_indexes(cur: sqlite3.Cursor) -> list[str]:
    indexes = [
        (
            "idx_commercial_contracts_unit_status",
            """
            CREATE INDEX idx_commercial_contracts_unit_status
            ON commercial_contracts(unit_id, status, valid_from, valid_to)
            """,
        ),
        (
            "idx_commercial_contract_items_contract",
            """
            CREATE INDEX idx_commercial_contract_items_contract
            ON commercial_contract_items(contract_id, is_active, valid_from, valid_to)
            """,
        ),
        (
            "idx_commercial_recipients_contract_status",
            """
            CREATE INDEX idx_commercial_recipients_contract_status
            ON commercial_contract_recipients(contract_id, notification_enabled, status)
            """,
        ),
        (
            "idx_commercial_recipients_telegram",
            """
            CREATE INDEX idx_commercial_recipients_telegram
            ON commercial_contract_recipients(telegram_user_id)
            """,
        ),
        (
            "idx_commercial_access_phone_contract_status",
            """
            CREATE INDEX idx_commercial_access_phone_contract_status
            ON commercial_access_phones(contract_id, status)
            """,
        ),
        (
            "idx_commercial_notifications_queue",
            """
            CREATE INDEX idx_commercial_notifications_queue
            ON commercial_notifications(status, created_at)
            """,
        ),
        (
            "idx_commercial_notifications_contract",
            """
            CREATE INDEX idx_commercial_notifications_contract
            ON commercial_notifications(contract_id, notification_type)
            """,
        ),
        (
            "idx_commercial_actions_contract_status",
            """
            CREATE INDEX idx_commercial_actions_contract_status
            ON commercial_access_actions(contract_id, action_status, action_type)
            """,
        ),
    ]

    if table_exists(cur, "charges"):
        indexes.extend([
            (
                "idx_charges_commercial_contract",
                """
                CREATE INDEX idx_charges_commercial_contract
                ON charges(commercial_contract_id, commercial_contract_item_id, commercial_due_date)
                """,
            ),
        ])

    if table_exists(cur, "payments"):
        indexes.append((
            "idx_payments_commercial_contract",
            """
            CREATE INDEX idx_payments_commercial_contract
            ON payments(commercial_contract_id, commercial_unit_id)
            """,
        ))

    if table_exists(cur, "cashbox_operations"):
        indexes.append((
            "idx_cashbox_operations_commercial_contract",
            """
            CREATE INDEX idx_cashbox_operations_commercial_contract
            ON cashbox_operations(commercial_contract_id, commercial_unit_id)
            """,
        ))

    created: list[str] = []
    for name, sql in indexes:
        if create_index_if_missing(cur, name, sql):
            created.append(name)

    # Один активный договор на одну единицу. Draft и history не ограничиваются.
    if not index_exists(cur, "ux_commercial_one_active_contract_per_unit"):
        cur.execute("""
            CREATE UNIQUE INDEX ux_commercial_one_active_contract_per_unit
            ON commercial_contracts(unit_id)
            WHERE status = 'ACTIVE'
        """)
        created.append("ux_commercial_one_active_contract_per_unit")

    if not index_exists(cur, "ux_commercial_notification_dedupe"):
        cur.execute("""
            CREATE UNIQUE INDEX ux_commercial_notification_dedupe
            ON commercial_notifications(dedupe_key)
        """)
        created.append("ux_commercial_notification_dedupe")

    if not index_exists(cur, "ux_commercial_phone_per_contract"):
        cur.execute("""
            CREATE UNIQUE INDEX ux_commercial_phone_per_contract
            ON commercial_access_phones(contract_id, phone_normalized)
        """)
        created.append("ux_commercial_phone_per_contract")

    return created


def create_or_replace_debt_views(cur: sqlite3.Cursor) -> tuple[bool, str]:
    """
    Вьюхи не вводят отдельный денежный контур: они считают задолженность
    из общего charges и обычных payment_allocations.
    """
    if not table_exists(cur, "charges"):
        return False, "charges отсутствует"

    if not table_exists(cur, "payment_allocations"):
        return False, "payment_allocations отсутствует"

    charge_columns = table_columns(cur, "charges")
    alloc_columns = table_columns(cur, "payment_allocations")

    if "amount" not in charge_columns:
        return False, "charges.amount отсутствует"

    allocation_amount = (
        "amount" if "amount" in alloc_columns
        else "allocated_amount" if "allocated_amount" in alloc_columns
        else None
    )
    if not allocation_amount:
        return False, "payment_allocations.amount/allocated_amount отсутствует"

    status_column = (
        "charge_status" if "charge_status" in charge_columns
        else "status" if "status" in charge_columns
        else None
    )
    status_condition = (
        f"AND COALESCE(c.{q(status_column)}, '') <> 'cancelled'"
        if status_column else ""
    )

    cur.execute("DROP VIEW IF EXISTS v_commercial_contract_charge_debt")
    cur.execute(f"""
        CREATE VIEW v_commercial_contract_charge_debt AS
        SELECT
            c.id AS charge_id,
            c.commercial_contract_id,
            c.commercial_contract_item_id,
            c.commercial_due_date,
            c.amount AS charge_amount,
            COALESCE(SUM(pa.{q(allocation_amount)}), 0) AS allocated_amount,
            CASE
                WHEN c.amount - COALESCE(SUM(pa.{q(allocation_amount)}), 0) > 0
                THEN c.amount - COALESCE(SUM(pa.{q(allocation_amount)}), 0)
                ELSE 0
            END AS outstanding_amount,
            COALESCE(i.blocks_phone_access_on_debt, 0) AS blocks_phone_access_on_debt
        FROM charges c
        LEFT JOIN payment_allocations pa ON pa.charge_id = c.id
        LEFT JOIN commercial_contract_items i
          ON i.id = c.commercial_contract_item_id
        WHERE c.commercial_contract_id IS NOT NULL
        {status_condition}
        GROUP BY c.id
    """)

    cur.execute("DROP VIEW IF EXISTS v_commercial_contract_debt_summary")
    cur.execute("""
        CREATE VIEW v_commercial_contract_debt_summary AS
        SELECT
            d.commercial_contract_id,
            COALESCE(SUM(d.outstanding_amount), 0) AS outstanding_amount,
            COALESCE(SUM(
                CASE
                    WHEN d.blocks_phone_access_on_debt = 1
                    THEN d.outstanding_amount
                    ELSE 0
                END
            ), 0) AS access_blocking_outstanding_amount,
            MIN(
                CASE
                    WHEN d.outstanding_amount > 0
                    THEN d.commercial_due_date
                    ELSE NULL
                END
            ) AS first_open_due_date
        FROM v_commercial_contract_charge_debt d
        GROUP BY d.commercial_contract_id
    """)

    return True, "созданы"


def validate_units(cur: sqlite3.Cursor) -> tuple[int, int]:
    """
    Отчётно считаем КП, но ничего не создаём автоматически.
    """
    if not table_exists(cur, "apartments"):
        return 0, 0

    columns = table_columns(cur, "apartments")
    if "unit_type" not in columns:
        return 0, 0

    cur.execute("""
        SELECT COUNT(*)
        FROM apartments
        WHERE unit_type = 'COMMERCIAL'
    """)
    commercial_count = int(cur.fetchone()[0] or 0)

    cur.execute("""
        SELECT COUNT(*)
        FROM apartments
        WHERE unit_type = 'COMMERCIAL'
          AND record_status = 'CONFIRMED'
    """)
    confirmed_count = int(cur.fetchone()[0] or 0)

    return commercial_count, confirmed_count


def write_report(
    report_file: Path,
    *,
    db_file: Path,
    apply: bool,
    new_tables: list[str],
    core_columns: dict[str, list[str]],
    billing_columns: dict[str, list[str]],
    indexes: list[str],
    service_status: str,
    debt_view_status: str,
    commercial_units: int,
    confirmed_units: int,
) -> None:
    lines = [
        "=" * 108,
        "МИГРАЦИЯ ДОГОВОРНОГО ЯДРА КОММЕРЧЕСКИХ ПОМЕЩЕНИЙ",
        "=" * 108,
        f"DB: {db_file}",
        f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        f"Apply: {apply}",
        "",
        "Логика уведомлений:",
        "  • должнику создаётся Telegram-уведомление, не SMS;",
        "  • SMS-команды GSM-шлагбауму здесь не создаются и не отправляются;",
        "  • отключение/восстановление доступа пока фиксируется как ручное действие оператора;",
        "  • один номер может иметь другие права доступа — физическое отключение требует проверки оператора.",
        "",
        "Новые таблицы:",
    ]
    lines.extend([f"  {name}" for name in new_tables] or ["  нет (уже существовали)"])

    lines.extend(["", "Новые поля в договорных таблицах:"])
    any_core = False
    for table, fields in core_columns.items():
        if fields:
            any_core = True
            lines.append(f"  {table}: {', '.join(fields)}")
    if not any_core:
        lines.append("  нет")

    lines.extend(["", "Связи с общим billing ledger:"])
    any_billing = False
    for table, fields in billing_columns.items():
        if fields:
            any_billing = True
            lines.append(f"  {table}: {', '.join(fields)}")
    if not any_billing:
        lines.append("  нет")

    lines.extend([
        "",
        f"Service catalog: {service_status}",
        f"Вьюхи задолженности: {debt_view_status}",
        f"Индивидуальных КП в реестре: {commercial_units}",
        f"Подтверждённых КП: {confirmed_units}",
        "",
        "Следующий этап:",
        "  заполнить договор и условия для конкретного КП;",
        "  привязать Telegram-представителя договора;",
        "  затем создавать начисления с commercial_contract_id и commercial_due_date.",
        "",
        "APPLIED" if apply else "DRY RUN COMPLETED - NO CHANGES SAVED",
    ])

    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Создать договорное ядро КП и Telegram-очередь уведомлений."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить изменения. По умолчанию dry-run.",
    )
    args = parser.parse_args()

    db_file = get_db_file()
    if not db_file.exists():
        raise SystemExit(f"Не найдена БД: {db_file}")

    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    if not table_exists(cur, "apartments"):
        conn.close()
        raise SystemExit("Не найдена таблица apartments.")
    if not table_exists(cur, "resident_accounts"):
        conn.close()
        raise SystemExit("Не найдена таблица resident_accounts.")

    conn.execute("BEGIN")

    new_tables = ensure_core_tables(cur)
    core_columns = ensure_core_columns(cur)
    billing_columns = ensure_billing_links(cur)
    indexes = ensure_indexes(cur)
    _created_service, service_status = seed_generic_commercial_service(cur)
    views_ok, debt_view_status = create_or_replace_debt_views(cur)
    commercial_units, confirmed_units = validate_units(cur)

    report_file = (
        paths.OSBB_EXPORTS_DIR / "commercial" /
        f"commercial_contract_core_{now_db().replace(':', '-').replace(' ', '_')}.txt"
    )

    apply_ok = bool(args.apply)
    if apply_ok:
        if audit_log:
            audit_log(
                conn=conn,
                operator_id="system",
                user_id="system",
                actor_type="system",
                action_type="commercial_contract_core_migration",
                table_name="commercial_contracts",
                row_id="",
                field_name="schema",
                old_value="",
                new_value="commercial contracts + Telegram notifications + access action audit",
                source_context="migrate_commercial_contract_core.py",
                comment=(
                    "Создано ядро индивидуальных договоров КП. "
                    "GSM/SMS-команды контроллеру не реализованы."
                ),
                extra={
                    "tables_created": new_tables,
                    "billing_columns": billing_columns,
                    "debt_views": debt_view_status,
                },
                commit=False,
            )
        conn.commit()
    else:
        conn.rollback()

    conn.close()

    write_report(
        report_file,
        db_file=db_file,
        apply=apply_ok,
        new_tables=new_tables,
        core_columns=core_columns,
        billing_columns=billing_columns,
        indexes=indexes,
        service_status=service_status,
        debt_view_status=("созданы" if views_ok else f"не созданы: {debt_view_status}"),
        commercial_units=commercial_units,
        confirmed_units=confirmed_units,
    )

    print("=" * 108)
    print("COMMERCIAL CONTRACT CORE")
    print("=" * 108)
    print("DB:", db_file)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", args.apply)
    print("New tables:", len(new_tables))
    print("New billing-link fields:", sum(len(x) for x in billing_columns.values()))
    print("Debt views:", "ready" if views_ok else f"not ready ({debt_view_status})")
    print("Commercial units:", commercial_units)
    print("Report:", report_file)
    print()
    print("APPLIED" if apply_ok else "DRY RUN COMPLETED - NO CHANGES SAVED")


if __name__ == "__main__":
    main()
