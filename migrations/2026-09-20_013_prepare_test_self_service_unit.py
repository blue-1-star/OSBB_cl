#!/usr/bin/env python3
"""Prepare the controlled live-DB test unit for resident self-service.

The unit is deliberately a technical test entity, not an invented numerical
apartment.  It contains no people, vehicles, charges or payments.  A small
registry links it to a test run so future cleanup is precise and auditable.

Run without ``--apply`` first.  Applying creates a SQLite backup and writes
one apartment plus the two lightweight test-governance tables in a transaction.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUP_DIR = ROOT / "Data" / "db" / "backups"

RUN_CODE = "TEST-SELF-SERVICE-2026-09"
UNIT_CODE = "TEST-SELF-01"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def backup_database(source: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"before_2026-09-20_013_test_self_service_unit_{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(source, target)
    return target


def setup_schema(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS test_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_code TEXT NOT NULL UNIQUE,
            purpose TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            started_at TEXT NOT NULL,
            finished_at TEXT,
            created_by TEXT NOT NULL,
            notes TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS test_data_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_run_id INTEGER NOT NULL,
            entity_table TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            purpose TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(test_run_id, entity_table, entity_id),
            FOREIGN KEY(test_run_id) REFERENCES test_runs(id)
        )
        """
    )


def validate_existing_unit(row: sqlite3.Row) -> None:
    expected = {
        "unit_type": "TECHNICAL",
        "unit_code": UNIT_CODE,
        "record_status": "TEST",
        "source": "INTERNAL_TEST",
    }
    wrong = [field for field, value in expected.items() if str(row[field] or "") != value]
    if wrong:
        raise RuntimeError(
            f"{UNIT_CODE} already exists but is not the controlled test unit; fields differ: {', '.join(wrong)}."
        )


def ensure_test_unit(cur: sqlite3.Cursor) -> tuple[int, bool]:
    row = cur.execute(
        """
        SELECT id, unit_type, unit_code, record_status, source
        FROM apartments WHERE apartment_number=? OR unit_code=?
        """,
        (UNIT_CODE, UNIT_CODE),
    ).fetchone()
    if row:
        validate_existing_unit(row)
        return int(row["id"]), False

    timestamp = now()
    cur.execute(
        """
        INSERT INTO apartments(
            apartment_number, entrance, entrance_number, total_area, object_type,
            status, source, notes, created_at, created_by,
            unit_type, unit_code, official_number, display_name, area_sqm,
            record_status, source_note, internal_note, unit_updated_at
        ) VALUES (?, 'TEST', 0, NULL, 'TECHNICAL',
                  'TEST', 'INTERNAL_TEST', ?, ?, 'migration/2026-09-20_013',
                  'TECHNICAL', ?, ?, 'Техническая квартира Self Service', NULL,
                  'TEST', ?, ?, ?)
        """,
        (
            UNIT_CODE,
            "Тестовый контур Self Service. Не жилая квартира; не использовать для начислений и отчётов.",
            timestamp,
            UNIT_CODE,
            UNIT_CODE,
            "Создано для контролируемой проверки ролей, кабинета жителя и заявок.",
            "TEST-SELF-SERVICE-2026-09; cleanup only through a controlled migration.",
            timestamp,
        ),
    )
    return int(cur.lastrowid), True


def ensure_run(cur: sqlite3.Cursor) -> int:
    timestamp = now()
    cur.execute(
        """
        INSERT INTO test_runs(run_code, purpose, status, started_at, created_by, notes)
        VALUES (?, ?, 'ACTIVE', ?, 'migration/2026-09-20_013', ?)
        ON CONFLICT(run_code) DO NOTHING
        """,
        (
            RUN_CODE,
            "Проверка self-service жителя и выдачи scoped-доступа в рабочей БД.",
            timestamp,
            "Все тестовые сущности должны быть зарегистрированы до последующей очистки.",
        ),
    )
    row = cur.execute("SELECT id FROM test_runs WHERE run_code=?", (RUN_CODE,)).fetchone()
    if not row:
        raise RuntimeError("Не удалось создать test run.")
    return int(row["id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        existing = conn.execute(
            "SELECT id, unit_type, unit_code, record_status, source FROM apartments WHERE apartment_number=? OR unit_code=?",
            (UNIT_CODE, UNIT_CODE),
        ).fetchone()
        print(f"DB: {db}")
        print(f"Test unit: {UNIT_CODE}")
        print("Existing unit:", "yes" if existing else "no")
        if existing:
            validate_existing_unit(existing)
        if not args.apply:
            print("DRY RUN ONLY — no changes saved. Use --apply to prepare the test unit.")
            return 0
    finally:
        conn.close()

    backup = backup_database(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        setup_schema(cur)
        run_id = ensure_run(cur)
        unit_id, created = ensure_test_unit(cur)
        cur.execute(
            """
            INSERT INTO test_data_registry(test_run_id, entity_table, entity_id, purpose, created_at)
            VALUES (?, 'apartments', ?, 'Техническая квартира для теста Self Service.', ?)
            ON CONFLICT(test_run_id, entity_table, entity_id) DO NOTHING
            """,
            (run_id, str(unit_id), now()),
        )
        if created:
            cur.execute(
                """
                INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                    old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
                VALUES (?, 'system', 'apartments', ?, 'create_test_unit', '*', '', ?, ?,
                        'system', 'migration/2026-09-20_013', 'migration', NULL)
                """,
                (
                    now(), str(unit_id),
                    f"apartment_number={UNIT_CODE}; unit_type=TECHNICAL; record_status=TEST; source=INTERNAL_TEST",
                    "Подготовлен контролируемый тестовый контур Self Service; начисления и реальные жители не создавались.",
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"APPLIED. Unit id={unit_id}; created={created}; test run id={run_id}")
    print(f"Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
