"""
Миграция «Касса и платежи v2» — исправленная совместимая версия.

Исправляет ситуацию, когда таблицы v2 уже существуют от ранних попыток,
но в них нет части полей. Скрипт:
- не удаляет данные;
- не изменяет существующие платежи, начисления, автомобили, квартиры;
- только добавляет недостающие поля, индексы и безопасные значения
  для технических полей новых таблиц;
- делает это только при --apply.

Важное исправление:
если cashier_batches уже существует, гарантированно добавляются как минимум
batch_number и entrance_number, а пустые batch_number получают технический
уникальный вид LEGACY-B-000001 и т. п.

Экспортируемая функция ensure_cashier_v2_schema(conn) используется
проверочным сценарием cashier_v2_preflight.py на sandbox-копии БД.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
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


def create_index_if_missing(cur: sqlite3.Cursor, name: str, sql: str) -> bool:
    if index_exists(cur, name):
        return False
    cur.execute(sql)
    return True


def create_payment_notices(cur: sqlite3.Cursor) -> bool:
    existed = table_exists(cur, "payment_notices")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payment_notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notice_number TEXT,
            notice_type TEXT,
            notice_status TEXT NOT NULL DEFAULT 'NEW',
            resident_account_id INTEGER,
            telegram_user_id TEXT,
            apartment_id INTEGER,
            apartment_number TEXT,
            declared_cashbox_code TEXT,
            declared_period_code TEXT,
            declared_service_code TEXT,
            declared_service_item_code TEXT,
            declared_amount REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'UAH',
            declared_at TEXT,
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
            updated_at TEXT
        )
    """)
    return not existed


def create_bank_transactions(cur: sqlite3.Cursor) -> bool:
    existed = table_exists(cur, "bank_transactions")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bank_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_ref TEXT,
            entry_status TEXT NOT NULL DEFAULT 'CONFIRMED',
            transaction_date TEXT,
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
            updated_at TEXT
        )
    """)
    return not existed


def create_cashier_batches(cur: sqlite3.Cursor) -> bool:
    existed = table_exists(cur, "cashier_batches")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashier_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_number TEXT,
            batch_kind TEXT,
            entry_status TEXT NOT NULL DEFAULT 'DRAFT',
            entrance_number TEXT,
            cashbox_code TEXT,
            period_code TEXT,
            service_code TEXT,
            service_item_code TEXT,
            receipt_date TEXT,
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


def create_cashier_batch_items(cur: sqlite3.Cursor) -> bool:
    existed = table_exists(cur, "cashier_batch_items")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashier_batch_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER,
            apartment_id INTEGER,
            apartment_number TEXT,
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
            updated_at TEXT
        )
    """)
    return not existed


def create_reconciliation_cases(cur: sqlite3.Cursor) -> bool:
    existed = table_exists(cur, "cashier_reconciliation_cases")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashier_reconciliation_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_type TEXT,
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
            description TEXT,
            operator_id TEXT,
            resolution_note TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)
    return not existed


V2_COLUMNS: dict[str, dict[str, str]] = {
    "payment_notices": {
        "notice_number": "TEXT",
        "notice_type": "TEXT",
        "notice_status": "TEXT NOT NULL DEFAULT 'NEW'",
        "resident_account_id": "INTEGER",
        "telegram_user_id": "TEXT",
        "apartment_id": "INTEGER",
        "apartment_number": "TEXT",
        "declared_cashbox_code": "TEXT",
        "declared_period_code": "TEXT",
        "declared_service_code": "TEXT",
        "declared_service_item_code": "TEXT",
        "declared_amount": "REAL NOT NULL DEFAULT 0",
        "currency": "TEXT NOT NULL DEFAULT 'UAH'",
        "declared_at": "TEXT",
        "resident_comment": "TEXT",
        "evidence_text": "TEXT",
        "matched_cashier_receipt_id": "INTEGER",
        "matched_bank_transaction_id": "INTEGER",
        "matched_payment_id": "INTEGER",
        "operator_id": "TEXT",
        "operator_note": "TEXT",
        "reviewed_at": "TEXT",
        "confirmed_at": "TEXT",
        "rejected_at": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "bank_transactions": {
        "transaction_ref": "TEXT",
        "entry_status": "TEXT NOT NULL DEFAULT 'CONFIRMED'",
        "transaction_date": "TEXT",
        "value_date": "TEXT",
        "apartment_id": "INTEGER",
        "apartment_number": "TEXT",
        "period_code": "TEXT",
        "service_code": "TEXT",
        "service_item_code": "TEXT",
        "amount": "REAL NOT NULL DEFAULT 0",
        "currency": "TEXT NOT NULL DEFAULT 'UAH'",
        "payer_text": "TEXT",
        "bank_name": "TEXT",
        "source_type": "TEXT NOT NULL DEFAULT 'manual_operator'",
        "source_ref": "TEXT",
        "payment_notice_id": "INTEGER",
        "payment_id": "INTEGER",
        "cashier_batch_id": "INTEGER",
        "operator_id": "TEXT",
        "operator_note": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "cashier_batches": {
        "batch_number": "TEXT",
        "batch_kind": "TEXT",
        "entry_status": "TEXT NOT NULL DEFAULT 'DRAFT'",
        "entrance_number": "TEXT",
        "cashbox_code": "TEXT",
        "period_code": "TEXT",
        "service_code": "TEXT",
        "service_item_code": "TEXT",
        "receipt_date": "TEXT",
        "included_count": "INTEGER NOT NULL DEFAULT 0",
        "excluded_count": "INTEGER NOT NULL DEFAULT 0",
        "total_amount": "REAL NOT NULL DEFAULT 0",
        "currency": "TEXT NOT NULL DEFAULT 'UAH'",
        "operator_id": "TEXT",
        "source_text": "TEXT",
        "confirmed_at": "TEXT",
        "voided_at": "TEXT",
        "void_reason": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "cashier_batch_items": {
        "batch_id": "INTEGER",
        "apartment_id": "INTEGER",
        "apartment_number": "TEXT",
        "charge_id": "INTEGER",
        "receipt_id": "INTEGER",
        "payment_id": "INTEGER",
        "amount_expected": "REAL",
        "amount_received": "REAL NOT NULL DEFAULT 0",
        "cashbox_code": "TEXT",
        "period_code": "TEXT",
        "service_code": "TEXT",
        "service_item_code": "TEXT",
        "item_status": "TEXT NOT NULL DEFAULT 'PENDING'",
        "exception_note": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "cashier_reconciliation_cases": {
        "case_type": "TEXT",
        "case_status": "TEXT NOT NULL DEFAULT 'OPEN'",
        "priority": "TEXT NOT NULL DEFAULT 'NORMAL'",
        "apartment_id": "INTEGER",
        "apartment_number": "TEXT",
        "amount": "REAL",
        "currency": "TEXT NOT NULL DEFAULT 'UAH'",
        "period_code": "TEXT",
        "service_code": "TEXT",
        "related_payment_id": "INTEGER",
        "related_receipt_id": "INTEGER",
        "related_notice_id": "INTEGER",
        "related_bank_transaction_id": "INTEGER",
        "related_batch_id": "INTEGER",
        "description": "TEXT",
        "operator_id": "TEXT",
        "resolution_note": "TEXT",
        "resolved_at": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
}


BASE_LINK_COLUMNS: dict[str, dict[str, str]] = {
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


def ensure_columns(
    cur: sqlite3.Cursor,
    specs: dict[str, dict[str, str]],
) -> dict[str, list[str]]:
    added: dict[str, list[str]] = {}
    for table, fields in specs.items():
        added[table] = []
        if not table_exists(cur, table):
            added[table].append("<table missing>")
            continue
        for field, definition in fields.items():
            if add_column_if_missing(cur, table, field, definition):
                added[table].append(field)
    return added


def backfill_batch_numbers(cur: sqlite3.Cursor) -> dict[str, int]:
    """
    Existing experimental tables may contain rows without batch_number.
    We preserve all data and only fill technical identifiers where absent.
    """
    result = {"blank_filled": 0, "duplicate_renamed": 0}
    if not table_exists(cur, "cashier_batches"):
        return result
    if "batch_number" not in columns(cur, "cashier_batches"):
        return result

    cur.execute("""
        SELECT id
        FROM cashier_batches
        WHERE batch_number IS NULL OR TRIM(batch_number) = ''
        ORDER BY id
    """)
    for (row_id,) in cur.fetchall():
        cur.execute(
            "UPDATE cashier_batches SET batch_number = ? WHERE id = ?",
            (f"LEGACY-B-{int(row_id):06d}", int(row_id)),
        )
        result["blank_filled"] += 1

    cur.execute("""
        SELECT batch_number
        FROM cashier_batches
        WHERE batch_number IS NOT NULL AND TRIM(batch_number) <> ''
        GROUP BY batch_number
        HAVING COUNT(*) > 1
    """)
    duplicates = [row[0] for row in cur.fetchall()]
    for value in duplicates:
        cur.execute(
            "SELECT id FROM cashier_batches WHERE batch_number = ? ORDER BY id",
            (value,),
        )
        duplicate_rows = [int(row[0]) for row in cur.fetchall()]
        # First row retains its existing number; subsequent rows receive
        # a deterministic suffix, so no historical record is deleted.
        for row_id in duplicate_rows[1:]:
            cur.execute(
                "UPDATE cashier_batches SET batch_number = ? WHERE id = ?",
                (f"{value}-LEGACY-{row_id}", row_id),
            )
            result["duplicate_renamed"] += 1
    return result


def backfill_v2_defaults(cur: sqlite3.Cursor) -> dict[str, int]:
    result: dict[str, int] = {}
    for table, field, default in [
        ("cashier_batches", "batch_kind", "LEGACY"),
        ("cashier_batches", "entry_status", "LEGACY"),
        ("cashier_batches", "currency", "UAH"),
        ("payment_notices", "notice_status", "NEW"),
        ("payment_notices", "currency", "UAH"),
        ("bank_transactions", "entry_status", "CONFIRMED"),
        ("bank_transactions", "currency", "UAH"),
        ("bank_transactions", "source_type", "legacy"),
        ("cashier_batch_items", "item_status", "LEGACY"),
        ("cashier_reconciliation_cases", "case_status", "OPEN"),
        ("cashier_reconciliation_cases", "priority", "NORMAL"),
        ("cashier_reconciliation_cases", "currency", "UAH"),
    ]:
        if table_exists(cur, table) and field in columns(cur, table):
            cur.execute(
                f"UPDATE {table} SET {field} = ? "
                f"WHERE {field} IS NULL OR TRIM(CAST({field} AS TEXT)) = ''",
                (default,),
            )
            result[f"{table}.{field}"] = int(cur.rowcount or 0)
    return result


def ensure_indexes(cur: sqlite3.Cursor) -> list[str]:
    candidates = [
        (
            "idx_payment_notices_status",
            "CREATE INDEX idx_payment_notices_status ON payment_notices(notice_status, declared_at)",
        ),
        (
            "idx_payment_notices_unit",
            "CREATE INDEX idx_payment_notices_unit ON payment_notices(apartment_id, apartment_number, notice_status)",
        ),
        (
            "idx_bank_transactions_ref",
            "CREATE INDEX idx_bank_transactions_ref ON bank_transactions(transaction_ref)",
        ),
        (
            "idx_bank_transactions_unit",
            "CREATE INDEX idx_bank_transactions_unit ON bank_transactions(apartment_id, apartment_number, transaction_date)",
        ),
        (
            "idx_cashier_batches_number",
            "CREATE INDEX idx_cashier_batches_number ON cashier_batches(batch_number)",
        ),
        (
            "idx_cashier_batches_status",
            "CREATE INDEX idx_cashier_batches_status ON cashier_batches(entry_status, receipt_date, entrance_number)",
        ),
        (
            "idx_cashier_batch_items_batch",
            "CREATE INDEX idx_cashier_batch_items_batch ON cashier_batch_items(batch_id, item_status)",
        ),
        (
            "idx_cashier_batch_items_unit",
            "CREATE INDEX idx_cashier_batch_items_unit ON cashier_batch_items(apartment_id, apartment_number, period_code)",
        ),
        (
            "idx_cashier_reconciliation_status",
            "CREATE INDEX idx_cashier_reconciliation_status ON cashier_reconciliation_cases(case_status, priority, created_at)",
        ),
        (
            "idx_cashier_reconciliation_unit",
            "CREATE INDEX idx_cashier_reconciliation_unit ON cashier_reconciliation_cases(apartment_id, apartment_number, case_status)",
        ),
    ]
    created = []
    for name, sql in candidates:
        if create_index_if_missing(cur, name, sql):
            created.append(name)

    optional = [
        ("payments", "payment_notice_id", "idx_payments_notice_v2"),
        ("payments", "bank_transaction_id", "idx_payments_bank_v2"),
        ("payments", "cashier_batch_id", "idx_payments_batch_v2"),
        ("cashier_receipts", "payment_notice_id", "idx_receipts_notice_v2"),
        ("cashier_receipts", "cashier_batch_id", "idx_receipts_batch_v2"),
        ("cashbox_operations", "cashier_batch_id", "idx_cashbox_ops_batch_v2"),
    ]
    for table, field, index_name in optional:
        if table_exists(cur, table) and field in columns(cur, table):
            if create_index_if_missing(
                cur,
                index_name,
                f"CREATE INDEX {index_name} ON {table}({field})",
            ):
                created.append(index_name)
    return created


def ensure_cashier_v2_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Изменяет только соединение conn. Preflight вызывает эту функцию для
    sandbox-копии; CLI --apply — для активной базы.
    """
    cur = conn.cursor()

    created = {
        "payment_notices": create_payment_notices(cur),
        "bank_transactions": create_bank_transactions(cur),
        "cashier_batches": create_cashier_batches(cur),
        "cashier_batch_items": create_cashier_batch_items(cur),
        "cashier_reconciliation_cases": create_reconciliation_cases(cur),
    }
    v2_fields = ensure_columns(cur, V2_COLUMNS)
    link_fields = ensure_columns(cur, BASE_LINK_COLUMNS)
    batch_backfill = backfill_batch_numbers(cur)
    defaults_backfill = backfill_v2_defaults(cur)
    indexes = ensure_indexes(cur)

    return {
        "tables_created": created,
        "v2_fields_added": v2_fields,
        "link_fields_added": link_fields,
        "batch_backfill": batch_backfill,
        "defaults_backfill": defaults_backfill,
        "indexes_added": indexes,
    }


def schema_plan(cur: sqlite3.Cursor) -> dict[str, list[str]]:
    plan: dict[str, list[str]] = {}
    for table, fields in V2_COLUMNS.items():
        missing = sorted(field for field in fields if field not in columns(cur, table))
        if not table_exists(cur, table):
            plan[table] = ["<table will be created>"]
        elif missing:
            plan[table] = missing
    for table, fields in BASE_LINK_COLUMNS.items():
        if not table_exists(cur, table):
            plan[table] = plan.get(table, []) + ["<base table missing>"]
            continue
        missing = sorted(field for field in fields if field not in columns(cur, table))
        if missing:
            plan[table] = plan.get(table, []) + missing
    return plan


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

    report_dir = paths.OSBB_EXPORTS_DIR / "cashier"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"cashier_v2_migration_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"

    lines = [
        "=" * 104,
        "CASHIER V2 MIGRATION — COMPATIBILITY REPAIR",
        "=" * 104,
        f"DB: {db}",
        f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        f"Apply: {args.apply}",
        "",
    ]

    if not args.apply:
        plan = schema_plan(cur)
        lines.append("Будущие изменения:")
        if plan:
            for table, fields in plan.items():
                lines.append(f"  {table}: {', '.join(fields)}")
        else:
            lines.append("  Структура уже соответствует v2.")
        lines.extend([
            "",
            "DRY RUN COMPLETED - NO CHANGES SAVED",
        ])
        conn.close()
        report.write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        print("Report:", report)
        return

    try:
        result = ensure_cashier_v2_schema(conn)

        if audit_log:
            audit_log(
                conn=conn,
                operator_id="system",
                user_id="system",
                actor_type="system",
                action_type="cashier_v2_schema_compatibility_repair",
                table_name="cashier_batches",
                row_id="",
                field_name="schema",
                old_value="partial v2 schema",
                new_value="complete compatible v2 schema",
                source_context="migrate_cashier_v2.py",
                comment=(
                    "Достроены недостающие поля v2 без удаления "
                    "исторических данных."
                ),
                extra=result,
                commit=False,
            )

        conn.commit()
        lines.extend([
            "APPLIED",
            "",
            f"Таблицы: {result['tables_created']}",
            f"Добавленные поля v2: {result['v2_fields_added']}",
            f"Связующие поля: {result['link_fields_added']}",
            f"Backfill batch_number: {result['batch_backfill']}",
            f"Backfill defaults: {result['defaults_backfill']}",
            f"Индексы: {result['indexes_added']}",
        ])
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print("Report:", report)


if __name__ == "__main__":
    main()
