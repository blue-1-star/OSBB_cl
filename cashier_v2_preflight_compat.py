"""
Безопасная проверка «Касса и платежи v2» с совместимой миграцией.

Не меняет исходную БД.
Создаёт SQLite-снимок в Data/db/sandbox/, применяет к снимку:
1) migrate_cashier_operator_editor.py
2) migrate_cashier_v2_compat.py
и проверяет структуру и переключение бота v2 в памяти.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import traceback
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOTS = ROOT / "Bots"
HANDLERS = BOTS / "handlers"
for folder in (ROOT, BOTS, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Не удалось подготовить модуль: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def copy_snapshot(source: Path, target: Path) -> None:
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def apply_v1(conn: sqlite3.Connection, m1) -> dict:
    cur = conn.cursor()
    m1.ensure_cashboxes_table(cur)
    m1.ensure_cashbox_operations_table(cur)
    receipt_fields = m1.ensure_cashier_receipts(cur)
    links = m1.ensure_link_columns(cur)

    for code, name, active, comment in m1.CASHBOXES:
        m1.upsert_cashbox(cur, code, name, active, comment)
    m1.upsert_cashbox(
        cur,
        "K",
        "K — агрегат консьержей (только история/отчёт)",
        0,
        "Не точка приёма. Новые операции вводить в K1–K6.",
    )

    for name, sql in [
        (
            "idx_cashier_receipts_status",
            "CREATE INDEX idx_cashier_receipts_status ON cashier_receipts(entry_status, receipt_date, id)",
        ),
        (
            "idx_cashier_receipts_cashbox",
            "CREATE INDEX idx_cashier_receipts_cashbox ON cashier_receipts(cashbox_code, receipt_date)",
        ),
        (
            "idx_cashier_receipts_apartment",
            "CREATE INDEX idx_cashier_receipts_apartment ON cashier_receipts(apartment_id, apartment_number, period_code)",
        ),
        (
            "idx_cashier_receipts_allocation",
            "CREATE INDEX idx_cashier_receipts_allocation ON cashier_receipts(allocation_status, entry_status)",
        ),
        (
            "idx_cashbox_operations_cashier_receipt",
            "CREATE INDEX idx_cashbox_operations_cashier_receipt ON cashbox_operations(cashier_receipt_id)",
        ),
        (
            "idx_cashbox_operations_transfer_group",
            "CREATE INDEX idx_cashbox_operations_transfer_group ON cashbox_operations(transfer_group_ref)",
        ),
    ]:
        m1.create_index_if_missing(cur, name, sql)

    if m1.table_exists(cur, "payments"):
        m1.create_index_if_missing(
            cur,
            "idx_payments_cashier_receipt",
            "CREATE INDEX idx_payments_cashier_receipt ON payments(cashier_receipt_id)",
        )

    cur.execute("SELECT cashbox_code FROM cashboxes")
    for (code,) in cur.fetchall():
        m1.recalc_balance(cur, code)

    return {"receipt_fields": receipt_fields, "links": links}


def validate_clone(conn: sqlite3.Connection) -> list[str]:
    cur = conn.cursor()
    required_tables = {
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
    errors = [
        "Нет таблицы: " + name
        for name in sorted(required_tables)
        if not table_exists(cur, name)
    ]

    required_fields = {
        "cashier_batches": {"batch_number", "entrance_number", "cashbox_code", "period_code"},
        "cashier_batch_items": {"batch_id", "apartment_number", "charge_id", "receipt_id", "payment_id"},
        "payment_notices": {"notice_number", "notice_type", "notice_status", "declared_amount"},
        "bank_transactions": {"transaction_ref", "transaction_date", "amount", "payment_id"},
        "payments": {"payment_notice_id", "bank_transaction_id", "cashier_batch_id", "payment_channel"},
        "cashier_receipts": {"payment_notice_id", "cashier_batch_id", "service_item_code"},
    }
    for table, wanted in required_fields.items():
        missing = sorted(wanted - columns(cur, table))
        if missing:
            errors.append(f"{table}: отсутствуют {', '.join(missing)}")
    return errors


def main() -> int:
    source_db = Path(paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE)
    sandbox_dir = source_db.parent / "sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    sandbox_db = sandbox_dir / f"{source_db.stem}_cashier_v2_compat_check_{stamp()}{source_db.suffix}"
    report = sandbox_dir / f"cashier_v2_preflight_compat_{stamp()}.txt"

    files = {
        "migration v1": ROOT / "migrate_cashier_operator_editor.py",
        "migration v2 compat": ROOT / "migrate_cashier_v2_compat.py",
        "core v2": ROOT / "cashier_v2_core.py",
        "operator v2": HANDLERS / "cashier_operator_v2.py",
        "client v2": HANDLERS / "client_portal_v2.py",
        "switcher": ROOT / "switch_parking_bot_to_cashier_v2.py",
        "bot": BOTS / "parking_bot.py",
        "base portal": HANDLERS / "client_portal.py",
        "base cashier": HANDLERS / "cashier_operator.py",
    }

    lines = [
        "=" * 100,
        "CASHIER V2 COMPAT PREFLIGHT — SANDBOX ONLY",
        "=" * 100,
        f"Source DB read-only: {source_db}",
        f"Sandbox DB: {sandbox_db}",
        f"Mode: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        "",
    ]
    errors: list[str] = []

    if not source_db.exists():
        errors.append(f"Не найдена БД: {source_db}")
    for label, file in files.items():
        if not file.exists():
            errors.append(f"Не найден {label}: {file}")

    if errors:
        lines.extend(["RESULT: NOT READY — nothing modified", *errors])
        report.write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        print("Report:", report)
        return 1

    lines.append("1. Компиляция")
    for label, file in files.items():
        try:
            compile(file.read_text(encoding="utf-8"), str(file), "exec")
            lines.append(f"   OK  {label}: {file.name}")
        except Exception as exc:
            errors.append(f"Compile {file.name}: {exc}")
            lines.append(f"   ERR {label}: {exc}")

    lines.append("")
    lines.append("2. Импортная совместимость")
    for label, file in [
        ("core v2", files["core v2"]),
        ("operator v2", files["operator v2"]),
        ("client v2", files["client v2"]),
    ]:
        try:
            load(file, f"_preflight_{label.replace(' ', '_')}")
            lines.append(f"   OK  {label}")
        except Exception:
            errors.append(f"Import {file.name}")
            lines.append(f"   ERR {label}")
            lines.append(traceback.format_exc())

    lines.append("")
    lines.append("3. Snapshot исходной БД")
    try:
        copy_snapshot(source_db, sandbox_db)
        lines.append("   OK  snapshot created")
    except Exception:
        errors.append("Не удалось создать snapshot")
        lines.append(traceback.format_exc())

    if sandbox_db.exists():
        lines.append("")
        lines.append("4. Миграции применяются только к snapshot")
        try:
            m1 = load(files["migration v1"], "_m1_compat")
            m2 = load(files["migration v2 compat"], "_m2_compat")
            conn = sqlite3.connect(sandbox_db)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                r1 = apply_v1(conn, m1)
                r2 = m2.ensure_cashier_v2_schema(conn)
                conn.commit()
            finally:
                conn.close()
            lines.append("   OK  v1 applied to snapshot")
            lines.append("   OK  v2 compat applied to snapshot")
            lines.append(f"       batch repair: {r2['batch_backfill']}")
            lines.append(f"       v2 field additions: {r2['v2_fields_added']}")
        except Exception:
            errors.append("Миграции не прошли на snapshot")
            lines.append("   ERR migration")
            lines.append(traceback.format_exc())

        lines.append("")
        lines.append("5. Проверка структуры snapshot")
        try:
            conn = sqlite3.connect(sandbox_db)
            try:
                structure_errors = validate_clone(conn)
            finally:
                conn.close()
            if structure_errors:
                errors.extend(structure_errors)
                for error in structure_errors:
                    lines.append("   ERR " + error)
            else:
                lines.append("   OK  структура v2 совместима")
        except Exception:
            errors.append("Ошибка проверки структуры snapshot")
            lines.append(traceback.format_exc())

    lines.append("")
    lines.append("6. Переключение parking_bot.py только в памяти")
    try:
        switch = load(files["switcher"], "_switch_compat")
        original = files["bot"].read_text(encoding="utf-8")
        patched, changes = switch.patch(original)
        compile(patched, "parking_bot_v2_memory.py", "exec")
        lines.append("   OK  bot switch compiles in memory")
        lines.append("       " + "; ".join(changes))
    except Exception:
        errors.append("Switch parking_bot v2 in memory failed")
        lines.append(traceback.format_exc())

    lines.append("")
    lines.append("=" * 100)
    if errors:
        lines.append("RESULT: NOT READY — ACTIVE DB AND BOT WERE NOT MODIFIED")
        lines.extend("  - " + error for error in errors)
        rc = 1
    else:
        lines.append("RESULT: READY FOR SANDBOX BOT TEST")
        lines.append("Active DB and active bot were not modified.")
        rc = 0
    lines.append("=" * 100)

    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print("Report:", report)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
