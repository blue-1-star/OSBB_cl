r"""
Безопасная проверочная сборка «Касса и платежи v2».

НИКОГДА не меняет исходную БД.
Сценарий работы:
1. Определяет активную TEST/WORK БД из config.py.
2. Делает её SQLite-снимок в Data/db/sandbox/.
3. Применяет обе миграции кассы ТОЛЬКО к копии:
      - migrate_cashier_operator_editor.py
      - migrate_cashier_v2.py
4. Проверяет таблицы и ключевые поля v2.
5. Проверяет компиляцию и импорты новых обработчиков.
6. Проверяет, можно ли переключить parking_bot.py на v2 в памяти,
   не записывая parking_bot.py.

Исходный osbb_test.db и osbb.db остаются только для чтения.

Запуск:
  g:\Programming\Py\venv\Scripts\python.exe G:\Programming\Py\OSBB\cashier_v2_preflight.py

Результат:
  - sandbox-копия БД;
  - отчёт в Data/db/sandbox/;
  - exit code 0 при готовности, 1 при найденной проблеме.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
BOTS_DIR = ROOT / "Bots"
HANDLERS_DIR = BOTS_DIR / "handlers"

for folder in (ROOT, BOTS_DIR, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Не удалось подготовить модуль: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall()}


def check_required_tables(cur: sqlite3.Cursor) -> list[str]:
    required = {
        "cashboxes",
        "cashbox_operations",
        "cashier_receipts",
        "payments",
        "charges",
        "payment_allocations",
        "service_catalog",
        "payment_notices",
        "bank_transactions",
        "cashier_batches",
        "cashier_batch_items",
        "cashier_reconciliation_cases",
    }
    return sorted(table for table in required if not table_exists(cur, table))


def check_required_fields(cur: sqlite3.Cursor) -> dict[str, list[str]]:
    expected = {
        "cashier_receipts": {
            "payment_notice_id",
            "cashier_batch_id",
            "service_item_code",
            "payment_id",
            "cashbox_operation_id",
        },
        "payments": {
            "payment_notice_id",
            "bank_transaction_id",
            "cashier_batch_id",
            "payment_channel",
        },
        "cashbox_operations": {
            "payment_notice_id",
            "cashier_batch_id",
            "cashier_receipt_id",
            "transfer_group_ref",
        },
        "payment_notices": {
            "notice_number",
            "notice_type",
            "notice_status",
            "resident_account_id",
            "declared_amount",
            "matched_payment_id",
        },
        "bank_transactions": {
            "transaction_ref",
            "transaction_date",
            "amount",
            "payment_id",
        },
        "cashier_batches": {
            "batch_number",
            "entrance_number",
            "cashbox_code",
            "period_code",
            "total_amount",
        },
        "cashier_batch_items": {
            "batch_id",
            "apartment_number",
            "charge_id",
            "receipt_id",
            "payment_id",
            "item_status",
        },
    }
    missing: dict[str, list[str]] = {}
    for table, fields in expected.items():
        absent = sorted(fields - columns(cur, table))
        if absent:
            missing[table] = absent
    return missing


def apply_v1_to_clone(conn: sqlite3.Connection, m1: Any) -> dict[str, Any]:
    cur = conn.cursor()
    m1.ensure_cashboxes_table(cur)
    m1.ensure_cashbox_operations_table(cur)
    receipt_added = m1.ensure_cashier_receipts(cur)
    links_added = m1.ensure_link_columns(cur)

    for code, name, active, comment in m1.CASHBOXES:
        m1.upsert_cashbox(cur, code, name, active, comment)
    m1.upsert_cashbox(
        cur,
        "K",
        "K — агрегат консьержей (только история/отчёт)",
        0,
        "Не точка приёма. Новые операции вводятся в K1–K6.",
    )

    index_sql = [
        (
            "idx_cashier_receipts_status",
            """
            CREATE INDEX idx_cashier_receipts_status
            ON cashier_receipts(entry_status, receipt_date, id)
            """,
        ),
        (
            "idx_cashier_receipts_cashbox",
            """
            CREATE INDEX idx_cashier_receipts_cashbox
            ON cashier_receipts(cashbox_code, receipt_date)
            """,
        ),
        (
            "idx_cashier_receipts_apartment",
            """
            CREATE INDEX idx_cashier_receipts_apartment
            ON cashier_receipts(apartment_id, apartment_number, period_code)
            """,
        ),
        (
            "idx_cashier_receipts_allocation",
            """
            CREATE INDEX idx_cashier_receipts_allocation
            ON cashier_receipts(allocation_status, entry_status)
            """,
        ),
    ]
    if m1.table_exists(cur, "payments"):
        index_sql.append((
            "idx_payments_cashier_receipt",
            """
            CREATE INDEX idx_payments_cashier_receipt
            ON payments(cashier_receipt_id)
            """,
        ))
    index_sql.extend([
        (
            "idx_cashbox_operations_cashier_receipt",
            """
            CREATE INDEX idx_cashbox_operations_cashier_receipt
            ON cashbox_operations(cashier_receipt_id)
            """,
        ),
        (
            "idx_cashbox_operations_transfer_group",
            """
            CREATE INDEX idx_cashbox_operations_transfer_group
            ON cashbox_operations(transfer_group_ref)
            """,
        ),
    ])

    indexes = []
    for name, sql in index_sql:
        if m1.create_index_if_missing(cur, name, sql):
            indexes.append(name)

    cur.execute("SELECT cashbox_code FROM cashboxes")
    for (code,) in cur.fetchall():
        m1.recalc_balance(cur, code)

    return {
        "receipt_fields_added": receipt_added,
        "link_fields_added": links_added,
        "indexes_added": indexes,
    }


def apply_v2_to_clone(conn: sqlite3.Connection, m2: Any) -> dict[str, Any]:
    cur = conn.cursor()
    base_required = {
        "cashier_receipts",
        "cashboxes",
        "cashbox_operations",
        "payments",
        "charges",
        "service_catalog",
    }
    missing = sorted(table for table in base_required if not m2.table_exists(cur, table))
    if missing:
        raise RuntimeError(
            "Базовая схема для v2 неполная: " + ", ".join(missing)
        )

    created = {
        "payment_notices": m2.create_table_payment_notices(cur),
        "bank_transactions": m2.create_table_bank_transactions(cur),
        "cashier_batches": m2.create_table_batches(cur),
        "cashier_batch_items": m2.create_table_batch_items(cur),
        "cashier_reconciliation_cases": m2.create_table_reconciliation_cases(cur),
    }
    links = m2.add_v2_link_columns(cur)
    indexes = m2.create_indexes(cur)
    return {
        "tables_created": created,
        "link_fields_added": links,
        "indexes_added": indexes,
    }


def copy_sqlite_snapshot(source: Path, target: Path) -> None:
    source_uri = f"file:{source.as_posix()}?mode=ro"
    src = sqlite3.connect(source_uri, uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def main() -> int:
    db = paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE
    db = Path(db)

    sandbox_dir = db.parent / "sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    sandbox_db = sandbox_dir / f"{db.stem}_cashier_v2_check_{stamp()}{db.suffix}"
    report = sandbox_dir / f"cashier_v2_preflight_{stamp()}.txt"

    required_files = {
        "migration v1": ROOT / "migrate_cashier_operator_editor.py",
        "migration v2": ROOT / "migrate_cashier_v2.py",
        "core v2": ROOT / "cashier_v2_core.py",
        "operator handler v2": HANDLERS_DIR / "cashier_operator_v2.py",
        "client portal v2": HANDLERS_DIR / "client_portal_v2.py",
        "bot switcher": ROOT / "switch_parking_bot_to_cashier_v2.py",
        "current parking_bot": BOTS_DIR / "parking_bot.py",
        "base client portal": HANDLERS_DIR / "client_portal.py",
        "base cashier handler": HANDLERS_DIR / "cashier_operator.py",
    }

    lines = [
        "=" * 100,
        "CASHIER V2 PREFLIGHT — SAFE SANDBOX CHECK",
        "=" * 100,
        f"Source DB (read-only): {db}",
        f"Sandbox DB: {sandbox_db}",
        f"Mode from config: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        "",
    ]
    errors: list[str] = []

    if not db.exists():
        errors.append(f"Не найдена исходная БД: {db}")

    missing_files = [
        f"{label}: {path}"
        for label, path in required_files.items()
        if not path.exists()
    ]
    if missing_files:
        errors.extend("Не найден файл: " + item for item in missing_files)

    if errors:
        lines.extend(["ПРОВЕРКА НЕ ЗАПУЩЕНА:", *errors])
        report.write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        print("Report:", report)
        return 1

    # 1. Syntax compilation: never touches DB.
    lines.append("1. Компиляция исходников")
    for label, path in required_files.items():
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            lines.append(f"   OK  {label}: {path.name}")
        except Exception as exc:
            errors.append(f"Syntax {path.name}: {exc}")
            lines.append(f"   ERR {label}: {exc}")

    # 2. Import checks: no DB write expected.
    lines.append("")
    lines.append("2. Импортная совместимость")
    try:
        load_module(ROOT / "cashier_v2_core.py", "_cashier_v2_core_preflight")
        lines.append("   OK  cashier_v2_core.py import")
    except Exception:
        message = traceback.format_exc()
        errors.append("Import cashier_v2_core.py")
        lines.append("   ERR cashier_v2_core.py import")
        lines.append(message)

    try:
        load_module(HANDLERS_DIR / "cashier_operator_v2.py", "_cashier_operator_v2_preflight")
        lines.append("   OK  cashier_operator_v2.py import")
    except Exception:
        message = traceback.format_exc()
        errors.append("Import cashier_operator_v2.py")
        lines.append("   ERR cashier_operator_v2.py import")
        lines.append(message)

    try:
        load_module(HANDLERS_DIR / "client_portal_v2.py", "_client_portal_v2_preflight")
        lines.append("   OK  client_portal_v2.py import")
    except Exception:
        message = traceback.format_exc()
        errors.append("Import client_portal_v2.py")
        lines.append("   ERR client_portal_v2.py import")
        lines.append(message)

    # 3. Snapshot. From this moment only sandbox can be modified.
    lines.append("")
    lines.append("3. Создание независимой SQLite-копии")
    try:
        copy_sqlite_snapshot(db, sandbox_db)
        lines.append(f"   OK  snapshot created: {sandbox_db.name}")
    except Exception:
        message = traceback.format_exc()
        errors.append("Не удалось создать sandbox-копию БД")
        lines.append("   ERR snapshot")
        lines.append(message)

    # 4. Apply migrations to clone only.
    if sandbox_db.exists():
        lines.append("")
        lines.append("4. Применение миграций только к sandbox-копии")
        try:
            m1 = load_module(ROOT / "migrate_cashier_operator_editor.py", "_m1_preflight")
            m2 = load_module(ROOT / "migrate_cashier_v2.py", "_m2_preflight")
            conn = sqlite3.connect(sandbox_db)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                v1_result = apply_v1_to_clone(conn, m1)
                v2_result = apply_v2_to_clone(conn, m2)
                conn.commit()
            finally:
                conn.close()
            lines.append("   OK  v1 base schema applied to clone")
            lines.append(f"       v1 receipt fields: {v1_result['receipt_fields_added']}")
            lines.append(f"       v1 links: {v1_result['link_fields_added']}")
            lines.append("   OK  v2 schema applied to clone")
            lines.append(f"       v2 tables: {v2_result['tables_created']}")
            lines.append(f"       v2 links: {v2_result['link_fields_added']}")
        except Exception:
            message = traceback.format_exc()
            errors.append("Миграция v1/v2 не прошла на sandbox-копии")
            lines.append("   ERR migration on clone")
            lines.append(message)

    # 5. Validate clone.
    if sandbox_db.exists():
        lines.append("")
        lines.append("5. Проверка структуры sandbox-копии")
        try:
            conn = sqlite3.connect(sandbox_db)
            try:
                cur = conn.cursor()
                missing_tables = check_required_tables(cur)
                missing_fields = check_required_fields(cur)
            finally:
                conn.close()

            if missing_tables:
                errors.append("В clone отсутствуют таблицы: " + ", ".join(missing_tables))
                lines.append("   ERR tables: " + ", ".join(missing_tables))
            else:
                lines.append("   OK  все обязательные таблицы существуют")

            if missing_fields:
                errors.append("В clone отсутствуют обязательные поля")
                for table, fields in missing_fields.items():
                    lines.append(f"   ERR {table}: {', '.join(fields)}")
            else:
                lines.append("   OK  все обязательные поля существуют")
        except Exception:
            message = traceback.format_exc()
            errors.append("Не удалось проверить clone")
            lines.append("   ERR clone validation")
            lines.append(message)

    # 6. In-memory patch validation for parking_bot.
    lines.append("")
    lines.append("6. Проверка переключения parking_bot.py в памяти")
    try:
        switch = load_module(ROOT / "switch_parking_bot_to_cashier_v2.py", "_switch_preflight")
        original = (BOTS_DIR / "parking_bot.py").read_text(encoding="utf-8")
        patched, changes = switch.patch(original)
        compile(patched, "parking_bot_v2_in_memory.py", "exec")
        lines.append("   OK  parking_bot v2 switch compiles in memory")
        lines.append("       " + "; ".join(changes))
    except Exception:
        message = traceback.format_exc()
        errors.append("Переключение parking_bot.py на v2 не прошло в памяти")
        lines.append("   ERR switch in memory")
        lines.append(message)

    lines.append("")
    lines.append("=" * 100)
    if errors:
        lines.append("RESULT: NOT READY — ACTIVE DB AND BOT WERE NOT MODIFIED")
        lines.append("Problems:")
        lines.extend(f"  - {item}" for item in errors)
        exit_code = 1
    else:
        lines.append("RESULT: READY FOR SANDBOX BOT TEST")
        lines.append(
            "Active DB was not modified. Sandbox copy contains the migration result."
        )
        exit_code = 0
    lines.append("=" * 100)

    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print("Report:", report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
