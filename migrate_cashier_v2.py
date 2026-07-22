"""
Миграция «Касса и платежи v2».

v2 заменяет запуск без отдельного полевого тестирования v1 и добавляет:
- уведомления жителей о передаче наличных / банковском переводе;
- банковские операции с защитой от повторного ввода по банковскому идентификатору;
- массовые пакеты платежей по подъезду;
- строки массового пакета с отдельной квартирой и суммой;
- очередь кассовой сверки: исторические импорты, бумажки, уведомления,
  неразнесённые оплаты и возможные дубли;
- связующие поля в существующих receipts / payments / cashbox_operations.

Ключевое правило:
Уведомление жителя НЕ является оплатой. Оно не меняет кассу, баланс и доступ,
пока оператор не подтвердит фактическое поступление / банковскую выписку.

Повторный запуск безопасен.
По умолчанию dry-run.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for folder in (ROOT, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB

try:
    from audit_logger import audit_log
except Exception:
    audit_log = None


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall()}


def add_column_if_missing(
    cur: sqlite3.Cursor,
    table: str,
    column: str,
    definition: str,
) -> bool:
    if column in columns(cur, table):
        return False
    cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
    return True


def index_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def create_index_if_missing(
    cur: sqlite3.Cursor,
    name: str,
    sql: str,
) -> bool:
    if index_exists(cur, name):
        return False
    cur.execute(sql)
    return True


def create_table_payment_notices(cur: sqlite3.Cursor) -> bool:
    existed = table_exists(cur, "payment_notices")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payment_notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notice_number TEXT NOT NULL UNIQUE,
            notice_type TEXT NOT NULL,
            notice_status TEXT NOT NULL DEFAULT 'NEW',
            resident_account_id INTEGER NOT NULL,
            telegram_user_id TEXT NOT NULL,
            apartment_id INTEGER,
            apartment_number TEXT,
            declared_cashbox_code TEXT,
            declared_period_code TEXT,
            declared_service_code TEXT,
            declared_service_item_code TEXT,
            declared_amount REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'UAH',
            declared_at TEXT NOT NULL,
            resident_comment TEXT,
            evidence_text TEXT,
            matched_cashier_receipt_id INTEGER,
            matched_bank_transaction_id INTEGER,
            matched_payment_id INTEGER,
            operator_id TEXT,
            operator_note TEXT,
            reviewed_at TEXT,
            confirmed_at TEXT,
            rejected_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            FOREIGN KEY(resident_account_id) REFERENCES resident_accounts(id),
            FOREIGN KEY(apartment_id) REFERENCES apartments(id)
        )
    """)
    return not existed


def create_table_bank_transactions(cur: sqlite3.Cursor) -> bool:
    existed = table_exists(cur, "bank_transactions")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bank_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_ref TEXT UNIQUE,
            entry_status TEXT NOT NULL DEFAULT 'CONFIRMED',
            transaction_date TEXT NOT NULL,
            value_date TEXT,
            apartment_id INTEGER,
            apartment_number TEXT,
            period_code TEXT,
            service_code TEXT,
            service_item_code TEXT,
            amount REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'UAH',
            payer_text TEXT,
            bank_name TEXT,
            source_type TEXT NOT NULL DEFAULT 'manual_operator',
            source_ref TEXT,
            payment_notice_id INTEGER,
            payment_id INTEGER,
            cashier_batch_id INTEGER,
            operator_id TEXT,
            operator_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            FOREIGN KEY(apartment_id) REFERENCES apartments(id),
            FOREIGN KEY(payment_notice_id) REFERENCES payment_notices(id)
        )
    """)
    return not existed


def create_table_batches(cur: sqlite3.Cursor) -> bool:
    existed = table_exists(cur, "cashier_batches")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashier_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_number TEXT NOT NULL UNIQUE,
            batch_kind TEXT NOT NULL,
            entry_status TEXT NOT NULL DEFAULT 'DRAFT',
            entrance_number TEXT,
            cashbox_code TEXT,
            period_code TEXT,
            service_code TEXT,
            service_item_code TEXT,
            receipt_date TEXT NOT NULL,
            included_count INTEGER NOT NULL DEFAULT 0,
            excluded_count INTEGER NOT NULL DEFAULT 0,
            total_amount REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'UAH',
            operator_id TEXT,
            source_text TEXT,
            confirmed_at TEXT,
            voided_at TEXT,
            void_reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)
    return not existed


def create_table_batch_items(cur: sqlite3.Cursor) -> bool:
    existed = table_exists(cur, "cashier_batch_items")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashier_batch_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            apartment_id INTEGER,
            apartment_number TEXT NOT NULL,
            charge_id INTEGER,
            receipt_id INTEGER,
            payment_id INTEGER,
            amount_expected REAL,
            amount_received REAL NOT NULL DEFAULT 0,
            cashbox_code TEXT,
            period_code TEXT,
            service_code TEXT,
            service_item_code TEXT,
            item_status TEXT NOT NULL DEFAULT 'PENDING',
            exception_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            FOREIGN KEY(batch_id) REFERENCES cashier_batches(id),
            FOREIGN KEY(apartment_id) REFERENCES apartments(id)
        )
    """)
    return not existed


def create_table_reconciliation_cases(cur: sqlite3.Cursor) -> bool:
    existed = table_exists(cur, "cashier_reconciliation_cases")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashier_reconciliation_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_type TEXT NOT NULL,
            case_status TEXT NOT NULL DEFAULT 'OPEN',
            priority TEXT NOT NULL DEFAULT 'NORMAL',
            apartment_id INTEGER,
            apartment_number TEXT,
            amount REAL,
            currency TEXT NOT NULL DEFAULT 'UAH',
            period_code TEXT,
            service_code TEXT,
            related_payment_id INTEGER,
            related_receipt_id INTEGER,
            related_notice_id INTEGER,
            related_bank_transaction_id INTEGER,
            related_batch_id INTEGER,
            description TEXT NOT NULL,
            operator_id TEXT,
            resolution_note TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)
    return not existed


def add_v2_link_columns(cur: sqlite3.Cursor) -> dict[str, list[str]]:
    additions: dict[str, list[str]] = {}

    specs = {
        "cashier_receipts": {
            "payment_notice_id": "INTEGER",
            "cashier_batch_id": "INTEGER",
            "service_item_code": "TEXT",
            "review_status": "TEXT",
        },
        "payments": {
            "payment_notice_id": "INTEGER",
            "bank_transaction_id": "INTEGER",
            "cashier_batch_id": "INTEGER",
            "payment_channel": "TEXT",
        },
        "cashbox_operations": {
            "payment_notice_id": "INTEGER",
            "cashier_batch_id": "INTEGER",
        },
    }

    for table, fields in specs.items():
        if not table_exists(cur, table):
            additions[table] = ["<table missing>"]
            continue
        additions[table] = []
        for field, definition in fields.items():
            if add_column_if_missing(cur, table, field, definition):
                additions[table].append(field)
    return additions


def create_indexes(cur: sqlite3.Cursor) -> list[str]:
    sqls = [
        (
            "idx_payment_notices_status",
            """
            CREATE INDEX idx_payment_notices_status
            ON payment_notices(notice_status, declared_at)
            """,
        ),
        (
            "idx_payment_notices_unit",
            """
            CREATE INDEX idx_payment_notices_unit
            ON payment_notices(apartment_id, apartment_number, notice_status)
            """,
        ),
        (
            "idx_bank_transactions_ref",
            """
            CREATE INDEX idx_bank_transactions_ref
            ON bank_transactions(transaction_ref)
            """,
        ),
        (
            "idx_bank_transactions_unit",
            """
            CREATE INDEX idx_bank_transactions_unit
            ON bank_transactions(apartment_id, apartment_number, transaction_date)
            """,
        ),
        (
            "idx_cashier_batches_status",
            """
            CREATE INDEX idx_cashier_batches_status
            ON cashier_batches(entry_status, receipt_date, entrance_number)
            """,
        ),
        (
            "idx_cashier_batch_items_batch",
            """
            CREATE INDEX idx_cashier_batch_items_batch
            ON cashier_batch_items(batch_id, item_status)
            """,
        ),
        (
            "idx_cashier_batch_items_unit",
            """
            CREATE INDEX idx_cashier_batch_items_unit
            ON cashier_batch_items(apartment_id, apartment_number, period_code)
            """,
        ),
        (
            "idx_cashier_reconciliation_status",
            """
            CREATE INDEX idx_cashier_reconciliation_status
            ON cashier_reconciliation_cases(case_status, priority, created_at)
            """,
        ),
        (
            "idx_cashier_reconciliation_unit",
            """
            CREATE INDEX idx_cashier_reconciliation_unit
            ON cashier_reconciliation_cases(apartment_id, apartment_number, case_status)
            """,
        ),
        (
            "idx_payments_notice_v2",
            """
            CREATE INDEX idx_payments_notice_v2
            ON payments(payment_notice_id)
            """,
        ) if table_exists(cur, "payments") and "payment_notice_id" in columns(cur, "payments") else ("", ""),
        (
            "idx_payments_bank_v2",
            """
            CREATE INDEX idx_payments_bank_v2
            ON payments(bank_transaction_id)
            """,
        ) if table_exists(cur, "payments") and "bank_transaction_id" in columns(cur, "payments") else ("", ""),
        (
            "idx_receipts_notice_v2",
            """
            CREATE INDEX idx_receipts_notice_v2
            ON cashier_receipts(payment_notice_id)
            """,
        ) if table_exists(cur, "cashier_receipts") and "payment_notice_id" in columns(cur, "cashier_receipts") else ("", ""),
    ]
    created = []
    for name, sql in sqls:
        if name and create_index_if_missing(cur, name, sql):
            created.append(name)
    return created


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = get_db_file()
    if not db.exists():
        raise SystemExit(f"Не найдена БД: {db}")

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    base_required = [
        "cashier_receipts",
        "cashboxes",
        "cashbox_operations",
        "payments",
        "charges",
        "service_catalog",
    ]
    missing = [table for table in base_required if not table_exists(cur, table)]
    if missing:
        raise SystemExit(
            "Сначала требуется миграция первой основы кассы и справочника услуг. "
            "Отсутствуют: " + ", ".join(missing)
        )

    tables_created = {
        "payment_notices": create_table_payment_notices(cur),
        "bank_transactions": create_table_bank_transactions(cur),
        "cashier_batches": create_table_batches(cur),
        "cashier_batch_items": create_table_batch_items(cur),
        "cashier_reconciliation_cases": create_table_reconciliation_cases(cur),
    }
    link_columns = add_v2_link_columns(cur)
    indexes = create_indexes(cur)

    report_dir = paths.OSBB_EXPORTS_DIR / "cashier"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / (
        f"cashier_v2_migration_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
    )

    lines = [
        "=" * 104,
        "CASHIER V2 MIGRATION",
        "=" * 104,
        f"DB: {db}",
        f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        f"Apply: {args.apply}",
        "",
        "Новые сущности:",
    ]
    lines.extend(
        f"  {name}: {'created' if created else 'already exists'}"
        for name, created in tables_created.items()
    )
    lines.extend([
        "",
        "Ключевые правила:",
        "  • O — основная касса охраны; K1–K6 — отдельные консьержи;",
        "  • BANK — банковский канал, не физическая касса;",
        "  • уведомление жителя не является оплатой;",
        "  • пакет подъезда создаёт отдельную строку на каждую квартиру;",
        "  • автоматическое разнесение допустимо только в подтверждённом пакете",
        "    с точной квартирой, точным начислением и точной суммой;",
        "  • исторические импорты и бумажки попадают в очередь сверки.",
        "",
        "Добавленные связи:",
    ])
    for table, added in link_columns.items():
        lines.append(f"  {table}: {', '.join(added) if added else 'нет'}")
    lines.append(f"Индексы: {', '.join(indexes) if indexes else 'нет'}")

    if args.apply:
        if audit_log:
            audit_log(
                conn=conn,
                operator_id="system",
                user_id="system",
                actor_type="system",
                action_type="cashier_v2_migration",
                table_name="cashier_batches",
                row_id="",
                field_name="schema",
                old_value="",
                new_value="payment notices + bank + batches + reconciliation",
                source_context="migrate_cashier_v2.py",
                comment=(
                    "Добавлена вторая версия кассы: банк, массовый ввод, "
                    "уведомления жителей и очередь сверки."
                ),
                extra={
                    "tables_created": tables_created,
                    "link_columns": link_columns,
                    "indexes": indexes,
                },
                commit=False,
            )
        conn.commit()
        lines.append("")
        lines.append("APPLIED")
    else:
        conn.rollback()
        lines.append("")
        lines.append("DRY RUN COMPLETED - NO CHANGES SAVED")

    report.write_text("\n".join(lines), encoding="utf-8")
    conn.close()

    print("=" * 104)
    print("CASHIER V2 MIGRATION")
    print("=" * 104)
    print("DB:", db)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", args.apply)
    print("Tables:", ", ".join(
        f"{name}={'new' if created else 'exists'}"
        for name, created in tables_created.items()
    ))
    print("Link fields:", sum(len(x) for x in link_columns.values()))
    print("Indexes:", len(indexes))
    print("Report:", report)
    print()
    print("APPLIED" if args.apply else "DRY RUN COMPLETED - NO CHANGES SAVED")


if __name__ == "__main__":
    main()
