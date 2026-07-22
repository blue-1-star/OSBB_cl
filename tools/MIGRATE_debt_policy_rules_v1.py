#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
MIGRATE_debt_policy_rules_v1.py

Создает минимальную таблицу политики задолженности:

  debt_policy_rules

Назначение:
  долг по одному service_code может блокировать заказ/выдачу/активацию другого service_code.

Безопасность:
  - по умолчанию DRY RUN;
  - запись только при --apply;
  - требует явный --db;
  - перед --apply делает backup .db;
  - не меняет charges/payments/remote_requests;
  - seed-правила можно запускать повторно, дубликаты не создаются.

PowerShell:

  python .\OSBB\tools\MIGRATE_debt_policy_rules_v1.py --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db"

  python .\OSBB\tools\MIGRATE_debt_policy_rules_v1.py --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db" --apply
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS debt_policy_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    debt_service_code TEXT NOT NULL,
    target_service_code TEXT NOT NULL,

    block_order INTEGER NOT NULL DEFAULT 0,
    block_issue INTEGER NOT NULL DEFAULT 0,
    block_activation INTEGER NOT NULL DEFAULT 0,

    severity TEXT NOT NULL DEFAULT 'HARD',
    is_active INTEGER NOT NULL DEFAULT 1,

    operator_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE (
        debt_service_code,
        target_service_code,
        block_order,
        block_issue,
        block_activation
    )
)
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_debt_policy_rules_debt_service ON debt_policy_rules(debt_service_code)",
    "CREATE INDEX IF NOT EXISTS idx_debt_policy_rules_target_service ON debt_policy_rules(target_service_code)",
    "CREATE INDEX IF NOT EXISTS idx_debt_policy_rules_active ON debt_policy_rules(is_active)",
]

SEED_RULES = [
    # debt_service_code, target_service_code, block_order, block_issue, block_activation, severity, note
    ("PARKING_DAY", "REMOTE_NEW", 1, 1, 0, "HARD", "Долг по дневной парковке блокирует заказ/выдачу нового пульта."),
    ("PARKING_NIGHT", "REMOTE_NEW", 1, 1, 0, "HARD", "Долг по ночной парковке блокирует заказ/выдачу нового пульта."),

    ("PARKING_DAY", "REMOTE_REPROGRAM", 1, 0, 0, "SOFT", "Долг по дневной парковке требует ручного решения по перепрошивке пульта."),
    ("PARKING_NIGHT", "REMOTE_REPROGRAM", 1, 0, 0, "SOFT", "Долг по ночной парковке требует ручного решения по перепрошивке пульта."),

    ("PARKING_DAY", "PHONE_ACCESS_CONNECT", 1, 0, 1, "HARD", "Долг по дневной парковке блокирует подключение телефонного доступа."),
    ("PARKING_NIGHT", "PHONE_ACCESS_CONNECT", 1, 0, 1, "HARD", "Долг по ночной парковке блокирует подключение телефонного доступа."),

    ("PARKING_DAY", "PHONE_ACCESS_CHANGE", 0, 0, 0, "SOFT", "Изменение номера телефона пока только ручной контроль."),
    ("PARKING_NIGHT", "PHONE_ACCESS_CHANGE", 0, 0, 0, "SOFT", "Изменение номера телефона пока только ручной контроль."),
]


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path: Path, readonly: bool) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    if not table_exists(cur, table):
        return []
    cur.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def existing_service_codes(cur: sqlite3.Cursor) -> set[str]:
    codes: set[str] = set()

    if table_exists(cur, "charges"):
        cols = table_columns(cur, "charges")
        for col in ("service_code", "base_service_code"):
            if col in cols:
                cur.execute(f"SELECT DISTINCT {col} FROM charges WHERE {col} IS NOT NULL AND TRIM({col}) <> ''")
                codes.update(str(row[0]).strip() for row in cur.fetchall() if row[0])

    if table_exists(cur, "service_items"):
        cols = table_columns(cur, "service_items")
        for col in ("service_code", "service_item_code", "item_code", "workflow_profile_code"):
            if col in cols:
                cur.execute(f"SELECT DISTINCT {col} FROM service_items WHERE {col} IS NOT NULL AND TRIM({col}) <> ''")
                codes.update(str(row[0]).strip() for row in cur.fetchall() if row[0])

    if table_exists(cur, "service_catalog"):
        cols = table_columns(cur, "service_catalog")
        for col in ("service_code", "code"):
            if col in cols:
                cur.execute(f"SELECT DISTINCT {col} FROM service_catalog WHERE {col} IS NOT NULL AND TRIM({col}) <> ''")
                codes.update(str(row[0]).strip() for row in cur.fetchall() if row[0])

    return codes


def backup_db(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"{db_path.stem}_before_debt_policy_rules_v1_{datetime.now():%Y-%m-%d_%H-%M-%S}{db_path.suffix}"
    shutil.copy2(db_path, dst)
    return dst


def count_rules(cur: sqlite3.Cursor) -> int:
    if not table_exists(cur, "debt_policy_rules"):
        return 0
    cur.execute("SELECT COUNT(*) FROM debt_policy_rules")
    return int(cur.fetchone()[0])


def insert_seed(cur: sqlite3.Cursor) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for debt_code, target_code, block_order, block_issue, block_activation, severity, note in SEED_RULES:
        try:
            cur.execute("""
                INSERT INTO debt_policy_rules (
                    debt_service_code,
                    target_service_code,
                    block_order,
                    block_issue,
                    block_activation,
                    severity,
                    is_active,
                    operator_note,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """, (
                debt_code,
                target_code,
                int(block_order),
                int(block_issue),
                int(block_activation),
                severity,
                note,
                now_db(),
                now_db(),
            ))
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1
    return inserted, skipped


def preview(cur: sqlite3.Cursor) -> list[str]:
    lines = []
    lines.append("Planned table: debt_policy_rules")
    lines.append("")
    lines.append("Columns:")
    lines.append(" - debt_service_code: долг по какому service_code является источником ограничения")
    lines.append(" - target_service_code: какая услуга/действие ограничивается")
    lines.append(" - block_order: блокировать заказ")
    lines.append(" - block_issue: блокировать выдачу")
    lines.append(" - block_activation: блокировать подключение/активацию")
    lines.append(" - severity: HARD или SOFT")
    lines.append(" - is_active: включено/выключено")
    lines.append("")
    lines.append("Seed rules:")
    for item in SEED_RULES:
        debt_code, target_code, order, issue, activation, severity, note = item
        flags = []
        if order:
            flags.append("order")
        if issue:
            flags.append("issue")
        if activation:
            flags.append("activation")
        lines.append(f" - {debt_code} -> {target_code} | {severity} | blocks: {', '.join(flags) or '-'} | {note}")

    codes = existing_service_codes(cur)
    lines.append("")
    lines.append(f"Known service-like codes in DB: {len(codes)}")

    missing = sorted({x[0] for x in SEED_RULES} | {x[1] for x in SEED_RULES} - codes)
    if missing:
        lines.append("Seed codes not found in existing service catalogs/charges:")
        for code in missing:
            lines.append(f" - {code}")
        lines.append("Note: this is not fatal for v1; target action codes may be policy-only.")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Explicit SQLite DB path. Required.")
    ap.add_argument("--apply", action="store_true", help="Actually create table and seed rules.")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    print("=" * 88)
    print("OSBB debt_policy_rules migration v1")
    print("=" * 88)
    print("Mode:", "APPLY" if args.apply else "DRY RUN / READ ONLY")
    print("DB:", db_path)
    print("")

    if not args.apply:
        conn = connect(db_path, readonly=True)
        try:
            cur = conn.cursor()
            for line in preview(cur):
                print(line)
            print("")
            print("DRY RUN COMPLETED. Re-run with --apply to migrate.")
            return 0
        finally:
            conn.close()

    backup = backup_db(db_path)

    conn = connect(db_path, readonly=False)
    try:
        cur = conn.cursor()
        cur.execute(TABLE_SQL)
        for sql in INDEX_SQL:
            cur.execute(sql)

        before = count_rules(cur)
        inserted, skipped = insert_seed(cur)
        after = count_rules(cur)

        conn.commit()

        print("Backup:", backup)
        print("Table debt_policy_rules: present")
        print("Rules before:", before)
        print("Seed inserted:", inserted)
        print("Seed skipped existing:", skipped)
        print("Rules after:", after)
        print("")
        print("APPLY COMPLETED")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
