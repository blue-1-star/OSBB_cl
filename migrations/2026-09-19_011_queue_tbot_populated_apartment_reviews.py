#!/usr/bin/env python3
"""Create a traceable operator queue for TBot vehicles at populated apartments.

The script selects only the current dry-run category
``REVIEW_NEW_VEHICLE_FOR_POPULATED_APARTMENT``.  It never modifies vehicles;
it creates verification tasks for an operator decision.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dry_run_tbot_vehicle_reconciliation import classify_rows


MAIN_DB = ROOT / "Data" / "db" / "osbb_test.db"
QUARANTINE_DB = ROOT / "Data" / "db" / "osbb_quarantine.db"
SOURCE_FILE = ROOT / "Data" / "raw" / "typed" / "parking_tbot2.xlsx"
BACKUP_DIR = ROOT / "Data" / "db" / "backups"
PIPELINE = "parking_tbot2.xlsx → quarantine → verification_tasks"
CREATED_BY = "migration/2026-09-19_011"
TASK_TYPE = "new_vehicle_for_populated_apartment"
CATEGORY = "REVIEW_NEW_VEHICLE_FOR_POPULATED_APARTMENT"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_rows() -> list[dict]:
    with sqlite3.connect(MAIN_DB) as main, sqlite3.connect(QUARANTINE_DB) as quarantine:
        rows = classify_rows(main, quarantine)
    return [row for row in rows if row["category"] == CATEGORY]


def add_column_if_missing(cur: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_schema(cur: sqlite3.Cursor) -> None:
    needed = ("data_import_batches", "verification_tasks", "vehicles", "audit_log")
    for table in needed:
        if not cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            raise RuntimeError(f"Не найдена обязательная таблица {table}.")
    add_column_if_missing(cur, "verification_tasks", "import_batch_id", "INTEGER")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_verification_tasks_import_batch "
        "ON verification_tasks(import_batch_id)"
    )


def apartment_vehicles(cur: sqlite3.Cursor, apartment_id: int) -> str:
    rows = cur.execute(
        """
        SELECT COALESCE(NULLIF(license_plate_normalized, ''), license_plate, '—'),
               COALESCE(NULLIF(car_model_normalized, ''), car_model, '—')
        FROM vehicles WHERE apartment_id=? ORDER BY id
        """,
        (apartment_id,),
    ).fetchall()
    return "; ".join(f"{plate} ({model})" for plate, model in rows) or "—"


def backup_database() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"before_2026-09-19_011_tbot_vehicle_reviews_{datetime.now():%Y%m%d-%H%M%S}.db"
    source = sqlite3.connect(MAIN_DB)
    backup = sqlite3.connect(target)
    try:
        source.backup(backup)
    finally:
        backup.close()
        source.close()
    return target


def apply(rows: list[dict]) -> tuple[int, int, Path]:
    if not rows:
        raise RuntimeError("Нет строк для очереди: отбор устарел или уже обработан.")
    if not SOURCE_FILE.exists():
        raise RuntimeError(f"Не найден исходный файл: {SOURCE_FILE}")
    with sqlite3.connect(QUARANTINE_DB) as quarantine:
        source_count = quarantine.execute("SELECT COUNT(*) FROM tbot_parking_import").fetchone()[0]
    backup = backup_database()
    conn = sqlite3.connect(MAIN_DB)
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        ensure_schema(cur)
        duplicate_ids = []
        for row in rows:
            found = cur.execute(
                """
                SELECT id FROM verification_tasks
                WHERE task_type=? AND source_name='tbot_parking' AND source_record_id=?
                  AND status IN ('new', 'in_progress')
                """,
                (TASK_TYPE, str(row["source_id"])),
            ).fetchone()
            if found:
                duplicate_ids.append(found[0])
        if duplicate_ids:
            raise RuntimeError(
                "Очередь уже частично создана: задачи " + ", ".join(map(str, duplicate_ids))
            )

        timestamp = now()
        cur.execute(
            """
            INSERT INTO data_import_batches(
                entity_type, source_file_name, source_file_path, source_file_sha256,
                pipeline, source_records_count, selected_records_count,
                inserted_records_count, skipped_records_count, status,
                created_at, applied_at, applied_by, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'APPLYING', ?, ?, ?, ?)
            """,
            (
                "vehicle_verification_tasks", SOURCE_FILE.name, str(SOURCE_FILE), sha256(SOURCE_FILE),
                PIPELINE, source_count, len(rows), timestamp, timestamp, CREATED_BY,
                "Очередь ручной проверки: новый автомобиль из TBot у квартиры с уже зарегистрированными авто.",
            ),
        )
        batch_id = cur.lastrowid
        inserted = 0
        for row in rows:
            existing = apartment_vehicles(cur, row["target_apartment_id"])
            comment = (
                f"Карантинная строка #{row['source_id']}; модель: {row['car_model'] or '—'}; "
                f"цвет: {row['car_color'] or '—'}; ФИО: {row['full_name'] or '—'}; "
                f"исходный статус: {row['status_raw'] or '—'}."
            )
            suggestion = (
                "Сверить с жильцом или первоисточником: добавить новый автомобиль, "
                "отклонить запись либо оставить задачу в работе."
            )
            cur.execute(
                """
                INSERT INTO verification_tasks(
                    apartment_id, apartment_number, task_group, task_type, priority, status,
                    source_name, source_record_id, object_table, object_id, field_name,
                    main_value, candidate_value, normalized_main_value, normalized_candidate_value,
                    suggestion, comment, created_at, created_by, import_batch_id
                ) VALUES (?, ?, 'vehicle', ?, 60, 'new', 'tbot_parking', ?, 'apartments', ?,
                          'vehicle_set', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["target_apartment_id"], row["apartment_number"], TASK_TYPE,
                    str(row["source_id"]), row["target_apartment_id"], existing,
                    row["license_plate"], existing, row["license_plate_normalized"],
                    suggestion, comment, timestamp, CREATED_BY, batch_id,
                ),
            )
            task_id = cur.lastrowid
            cur.execute(
                """
                INSERT INTO audit_log(
                    event_time, username, table_name, record_id, action, field_name,
                    old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id
                ) VALUES (?, 'system', 'verification_tasks', ?, 'insert', '*', '', ?, ?,
                          'system', ?, 'parking_tbot2.xlsx', NULL)
                """,
                (
                    timestamp, str(task_id),
                    f"task_type={TASK_TYPE}; apartment={row['apartment_number']}; candidate={row['license_plate_normalized']}",
                    f"Партия проверки #{batch_id}; {comment}", CREATED_BY,
                ),
            )
            inserted += 1
        cur.execute(
            "UPDATE data_import_batches SET inserted_records_count=?, status='APPLIED' WHERE id=?",
            (inserted, batch_id),
        )
        conn.commit()
        return batch_id, inserted, backup
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue TBot new-vehicle conflicts for operator verification.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rows = select_rows()
    print(f"Selected {CATEGORY}: {len(rows)}")
    for row in rows:
        print(f"quarantine #{row['source_id']}: кв.{row['apartment_number']} | {row['license_plate_normalized']} | {row['car_model'] or '-'}")
    if not args.apply:
        print("DRY RUN ONLY — main DB will not change.")
        return 0
    batch_id, inserted, backup = apply(rows)
    print(f"APPLIED batch #{batch_id}; verification tasks inserted: {inserted}")
    print(f"Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
