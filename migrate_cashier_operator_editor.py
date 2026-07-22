"""
Миграция операторского кассового ввода.

Создаёт:
- отдельные точки приёма K1–K6;
- O — основную кассу охраны;
- C — центральную кассу;
- cashier_receipts — электронную карточку бумажки/приёма денег;
- связи receipts -> payments -> cashbox_operations.

ВАЖНО
------
K — не касса для ввода, а агрегатный исторический/отчётный код.
Он остаётся в БД для старых операций, но помечается неактивным.
Новые поступления вводятся только в O, K1–K6 или C.

Сценарии:
1) Деньги физически получены:
   создаётся cashier_receipt + payment + cashbox_operation.
   Автоматического распределения на начисления НЕТ.
2) Есть только бумажка, денег физически нет:
   создаётся cashier_receipt типа PAPER_NOTE.
   Платёж и остаток кассы не меняются.
3) Нераспределённый платёж оператор позднее вручную разносит на начисления.

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


CASHBOXES = [
    (
        "O",
        "Касса охраны — основная",
        1,
        "Основная точка приёма наличных. Деньги, принятые охраной из рук в руки.",
    ),
    (
        "K1",
        "Консьерж 1 — точка приёма",
        1,
        "Деньги физически находятся у консьержа подъезда 1 до передачи.",
    ),
    (
        "K2",
        "Консьерж 2 — точка приёма",
        1,
        "Деньги физически находятся у консьержа подъезда 2 до передачи.",
    ),
    (
        "K3",
        "Консьерж 3 — точка приёма",
        1,
        "Деньги физически находятся у консьержа подъезда 3 до передачи.",
    ),
    (
        "K4",
        "Консьерж 4 — точка приёма",
        1,
        "Деньги физически находятся у консьержа подъезда 4 до передачи.",
    ),
    (
        "K5",
        "Консьерж 5 — точка приёма",
        1,
        "Деньги физически находятся у консьержа подъезда 5 до передачи.",
    ),
    (
        "K6",
        "Консьерж 6 — точка приёма",
        1,
        "Деньги физически находятся у консьержа подъезда 6 до передачи.",
    ),
    (
        "C",
        "Центральная касса",
        1,
        "Центральная касса для инкассации и передачи остатков.",
    ),
    (
        "BANK",
        "Банк / безнал",
        1,
        "Безналичные оплаты. Не используется для ручного ввода наличных.",
    ),
]


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


def ensure_cashboxes_table(cur: sqlite3.Cursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashboxes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cashbox_code TEXT NOT NULL UNIQUE,
            cashbox_name TEXT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'UAH',
            initial_balance REAL NOT NULL DEFAULT 0,
            current_balance REAL NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)


def ensure_cashbox_operations_table(cur: sqlite3.Cursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashbox_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_date TEXT NOT NULL,
            cashbox_code TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'UAH',
            period_code TEXT,
            apartment_number TEXT,
            vehicle_id INTEGER,
            service_code TEXT,
            payment_id INTEGER,
            charge_id INTEGER,
            batch_id TEXT,
            source_type TEXT DEFAULT 'cashier_journal',
            source_ref TEXT,
            operator_id TEXT,
            actor_type TEXT DEFAULT 'operator',
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)


def ensure_cashier_receipts(cur: sqlite3.Cursor) -> list[str]:
    existed = table_exists(cur, "cashier_receipts")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashier_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_number TEXT NOT NULL UNIQUE,
            receipt_kind TEXT NOT NULL,
            entry_status TEXT NOT NULL DEFAULT 'DRAFT',
            cashbox_code TEXT,
            receipt_date TEXT NOT NULL,
            document_date TEXT,
            origin_kind TEXT NOT NULL DEFAULT 'HAND_TO_HAND',
            origin_cashbox_code TEXT,
            payer_name TEXT,
            apartment_id INTEGER,
            apartment_number TEXT,
            service_hint TEXT,
            period_code TEXT,
            amount REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'UAH',
            evidence_type TEXT NOT NULL DEFAULT 'ORAL',
            paper_ref TEXT,
            source_text TEXT,
            designation_status TEXT NOT NULL DEFAULT 'UNSPECIFIED',
            allocation_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
            payment_id INTEGER,
            cashbox_operation_id INTEGER,
            void_payment_id INTEGER,
            void_cashbox_operation_id INTEGER,
            operator_id TEXT,
            confirmed_by TEXT,
            confirmed_at TEXT,
            voided_by TEXT,
            voided_at TEXT,
            void_reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)

    added: list[str] = []
    for name, definition in {
        "receipt_number": "TEXT",
        "receipt_kind": "TEXT NOT NULL DEFAULT 'CASH_RECEIVED'",
        "entry_status": "TEXT NOT NULL DEFAULT 'DRAFT'",
        "cashbox_code": "TEXT",
        "receipt_date": "TEXT",
        "document_date": "TEXT",
        "origin_kind": "TEXT NOT NULL DEFAULT 'HAND_TO_HAND'",
        "origin_cashbox_code": "TEXT",
        "payer_name": "TEXT",
        "apartment_id": "INTEGER",
        "apartment_number": "TEXT",
        "service_hint": "TEXT",
        "period_code": "TEXT",
        "amount": "REAL NOT NULL DEFAULT 0",
        "currency": "TEXT NOT NULL DEFAULT 'UAH'",
        "evidence_type": "TEXT NOT NULL DEFAULT 'ORAL'",
        "paper_ref": "TEXT",
        "source_text": "TEXT",
        "designation_status": "TEXT NOT NULL DEFAULT 'UNSPECIFIED'",
        "allocation_status": "TEXT NOT NULL DEFAULT 'NOT_APPLICABLE'",
        "payment_id": "INTEGER",
        "cashbox_operation_id": "INTEGER",
        "void_payment_id": "INTEGER",
        "void_cashbox_operation_id": "INTEGER",
        "operator_id": "TEXT",
        "confirmed_by": "TEXT",
        "confirmed_at": "TEXT",
        "voided_by": "TEXT",
        "voided_at": "TEXT",
        "void_reason": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    }.items():
        if add_column_if_missing(cur, "cashier_receipts", name, definition):
            added.append(name)

    return added if existed else ["<table cashier_receipts created>"]


def ensure_link_columns(cur: sqlite3.Cursor) -> dict[str, list[str]]:
    result = {"payments": [], "cashbox_operations": []}

    if table_exists(cur, "payments"):
        for column, definition in {
            "cashier_receipt_id": "INTEGER",
            "cashier_entry_status": "TEXT",
        }.items():
            if add_column_if_missing(cur, "payments", column, definition):
                result["payments"].append(column)

    if table_exists(cur, "cashbox_operations"):
        for column, definition in {
            "cashier_receipt_id": "INTEGER",
            "transfer_group_ref": "TEXT",
        }.items():
            if add_column_if_missing(cur, "cashbox_operations", column, definition):
                result["cashbox_operations"].append(column)

    return result


def upsert_cashbox(cur: sqlite3.Cursor, code: str, name: str, active: int, comment: str) -> str:
    cur.execute(
        "SELECT id FROM cashboxes WHERE cashbox_code = ?",
        (code,),
    )
    row = cur.fetchone()

    if row:
        cur.execute("""
            UPDATE cashboxes
            SET
                cashbox_name = ?,
                is_active = ?,
                comment = ?,
                updated_at = ?
            WHERE cashbox_code = ?
        """, (name, active, comment, now_db(), code))
        return "updated"

    cur.execute("""
        INSERT INTO cashboxes (
            cashbox_code,
            cashbox_name,
            currency,
            initial_balance,
            current_balance,
            is_active,
            comment,
            created_at,
            updated_at
        )
        VALUES (?, ?, 'UAH', 0, 0, ?, ?, ?, ?)
    """, (code, name, active, comment, now_db(), now_db()))
    return "created"


def recalc_balance(cur: sqlite3.Cursor, cashbox_code: str) -> float:
    cur.execute(
        "SELECT initial_balance FROM cashboxes WHERE cashbox_code = ?",
        (cashbox_code,),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"Не найдена касса {cashbox_code}")

    initial = float(row[0] or 0)
    cur.execute("""
        SELECT COALESCE(SUM(
            CASE
                WHEN direction = 'in' THEN amount
                WHEN direction = 'out' THEN -amount
                ELSE 0
            END
        ), 0)
        FROM cashbox_operations
        WHERE cashbox_code = ?
    """, (cashbox_code,))
    current = initial + float(cur.fetchone()[0] or 0)
    cur.execute("""
        UPDATE cashboxes
        SET current_balance = ?, updated_at = ?
        WHERE cashbox_code = ?
    """, (current, now_db(), cashbox_code))
    return current


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

    ensure_cashboxes_table(cur)
    ensure_cashbox_operations_table(cur)
    receipt_added = ensure_cashier_receipts(cur)
    links_added = ensure_link_columns(cur)

    changes: list[str] = []
    for code, name, active, comment in CASHBOXES:
        changes.append(f"{code}:{upsert_cashbox(cur, code, name, active, comment)}")

    # K остается для исторических операций и агрегированных отчётов,
    # но новые денежные записи в него вводить нельзя.
    changes.append(
        "K:" + upsert_cashbox(
            cur,
            "K",
            "K — агрегат консьержей (только история/отчёт)",
            0,
            "Не точка приёма. Новые операции вводить в K1–K6.",
        )
    )

    created_indexes: list[str] = []
    for name, sql in [
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
        (
            "idx_payments_cashier_receipt",
            """
            CREATE INDEX idx_payments_cashier_receipt
            ON payments(cashier_receipt_id)
            """,
        ) if table_exists(cur, "payments") else ("", ""),
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
    ]:
        if name and create_index_if_missing(cur, name, sql):
            created_indexes.append(name)

    balances: dict[str, float] = {}
    cur.execute("SELECT cashbox_code FROM cashboxes")
    for (code,) in cur.fetchall():
        balances[code] = recalc_balance(cur, code)

    report_dir = paths.OSBB_EXPORTS_DIR / "cashier"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"cashier_operator_migration_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"

    lines = [
        "=" * 104,
        "CASHIER OPERATOR EDITOR MIGRATION",
        "=" * 104,
        f"DB: {db}",
        f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        f"Apply: {args.apply}",
        "",
        "Точки приёма:",
        "  O  — касса охраны, основная;",
        "  K1..K6 — отдельные кассы консьержей;",
        "  C  — центральная касса;",
        "  K  — агрегат истории/отчёта, новые операции запрещены.",
        "",
        "Сценарий «деньги из рук в руки, кв. X, за июль»:",
        "  1. Оператор выбирает O — касса охраны.",
        "  2. Фиксирует фактическую дату приёма денег.",
        "  3. Указывает квартиру, 2026-07, сумму и дословное основание.",
        "  4. Создаётся receipt + payment + операция O.",
        "  5. Деньги НЕ разносятся автоматически на начисления.",
        "  6. Позже оператор вручную выбирает конкретное начисление для разнесения.",
        "",
        "Бумажка без физически полученных денег:",
        "  создаётся PAPER_NOTE; платёж и остаток кассы не изменяются.",
        "",
        "Изменения касс:",
    ]
    lines.extend([f"  {item}" for item in changes])
    lines.extend([
        "",
        f"Новые/добавленные поля receipts: {', '.join(receipt_added) if receipt_added else 'нет'}",
        f"Связи payments: {', '.join(links_added['payments']) if links_added['payments'] else 'нет'}",
        f"Связи cashbox_operations: {', '.join(links_added['cashbox_operations']) if links_added['cashbox_operations'] else 'нет'}",
        f"Индексы: {', '.join(created_indexes) if created_indexes else 'нет'}",
        "",
        "Остатки после пересчёта:",
    ])
    lines.extend([f"  {code}: {amount:.2f} UAH" for code, amount in sorted(balances.items())])

    if args.apply:
        if audit_log:
            audit_log(
                conn=conn,
                operator_id="system",
                user_id="system",
                actor_type="system",
                action_type="cashier_operator_editor_migration",
                table_name="cashier_receipts",
                row_id="",
                field_name="schema,cashboxes",
                old_value="",
                new_value="cashier receipts + O/K1-K6/C",
                source_context="migrate_cashier_operator_editor.py",
                comment=(
                    "Создана основа ручного кассового ввода. "
                    "Авторазнесение оплат отключено."
                ),
                extra={
                    "cashboxes": changes,
                    "receipt_columns": receipt_added,
                    "link_columns": links_added,
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
    print("CASHIER OPERATOR EDITOR MIGRATION")
    print("=" * 104)
    print("DB:", db)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", args.apply)
    print("Receipt table / fields:", ", ".join(receipt_added) if receipt_added else "ready")
    print("Cashboxes:", ", ".join(changes))
    print("Indexes:", len(created_indexes))
    print("Report:", report)
    print()
    print("APPLIED" if args.apply else "DRY RUN COMPLETED - NO CHANGES SAVED")


if __name__ == "__main__":
    main()
