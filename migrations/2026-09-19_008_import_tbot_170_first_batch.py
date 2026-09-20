#!/usr/bin/env python3
"""Create the first traceable vehicle-import batch: apartment 170 from TBot.

The command is a dry run by default.  With ``--apply`` it imports only the
two explicitly listed quarantine rows (AA5740PH and AA8257PI), while retaining
their original record IDs and the source file fingerprint in the main DB.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_DB = ROOT / "Data" / "db" / "osbb_test.db"
QUARANTINE_DB = ROOT / "Data" / "db" / "osbb_quarantine.db"
SOURCE_FILE = ROOT / "Data" / "raw" / "typed" / "parking_tbot2.xlsx"
BACKUP_DIR = ROOT / "Data" / "db" / "backups"
SOURCE_ROW_IDS = (83, 84)
EXPECTED = {
    83: ("170", "AA5740PH"),
    84: ("170", "AA8257PI"),
}
PIPELINE = "parking_tbot2.xlsx → quarantine → main vehicles"
CREATED_BY = "migration/2026-09-19_008"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_schema(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS data_import_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            source_file_name TEXT NOT NULL,
            source_file_path TEXT NOT NULL,
            source_file_sha256 TEXT NOT NULL,
            pipeline TEXT NOT NULL,
            source_records_count INTEGER NOT NULL,
            selected_records_count INTEGER NOT NULL,
            inserted_records_count INTEGER NOT NULL DEFAULT 0,
            skipped_records_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            applied_at TEXT,
            applied_by TEXT,
            notes TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_import_batch_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            source_db TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            apartment_number TEXT,
            license_plate_raw TEXT,
            license_plate_normalized TEXT,
            source_payload_json TEXT NOT NULL,
            decision TEXT NOT NULL,
            decision_reason TEXT,
            vehicle_id INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(batch_id, source_table, source_record_id),
            FOREIGN KEY(batch_id) REFERENCES data_import_batches(id),
            FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_import_batches_created ON data_import_batches(created_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_vehicle_import_batch_items_batch ON vehicle_import_batch_items(batch_id)"
    )


def load_source_rows() -> list[sqlite3.Row]:
    if not QUARANTINE_DB.exists():
        raise RuntimeError(f"Не найдена карантинная БД: {QUARANTINE_DB}")
    conn = sqlite3.connect(QUARANTINE_DB)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ", ".join("?" for _ in SOURCE_ROW_IDS)
        rows = conn.execute(
            f"""
            SELECT id, apartment_number, full_name, phone_raw, car_model, car_color,
                   license_plate, license_plate_normalized, car_model_normalized,
                   car_color_normalized, status_raw, ownership_type, source, imported_at
            FROM tbot_parking_import
            WHERE id IN ({placeholders})
            ORDER BY id
            """,
            SOURCE_ROW_IDS,
        ).fetchall()
    finally:
        conn.close()
    if len(rows) != len(SOURCE_ROW_IDS):
        raise RuntimeError("В карантине не найдены обе ожидаемые строки кв. 170.")
    for row in rows:
        expected_apartment, expected_plate = EXPECTED[row["id"]]
        if str(row["apartment_number"]).strip() != expected_apartment:
            raise RuntimeError(f"Строка #{row['id']}: неожиданная квартира.")
        if str(row["license_plate_normalized"] or "").upper() != expected_plate:
            raise RuntimeError(f"Строка #{row['id']}: неожиданный госномер.")
    return rows


def validate_main(cur: sqlite3.Cursor, rows: list[sqlite3.Row]) -> int:
    apartment = cur.execute(
        "SELECT id FROM apartments WHERE apartment_number='170'"
    ).fetchone()
    if not apartment:
        raise RuntimeError("В основной БД не найдена физическая квартира 170.")
    apartment_id = apartment[0]
    for row in rows:
        duplicate = cur.execute(
            """
            SELECT id, apartment_id FROM vehicles
            WHERE UPPER(COALESCE(license_plate_normalized, license_plate))=?
            """,
            (row["license_plate_normalized"],),
        ).fetchone()
        if duplicate:
            raise RuntimeError(
                f"Госномер {row['license_plate_normalized']} уже есть в vehicles: id={duplicate[0]}."
            )
    return apartment_id


def backup_database() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"before_2026-09-19_008_tbot_170_{datetime.now():%Y%m%d-%H%M%S}.db"
    source = sqlite3.connect(MAIN_DB)
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup


def apply() -> tuple[int, list[int]]:
    rows = load_source_rows()
    if not SOURCE_FILE.exists():
        raise RuntimeError(f"Не найден исходный файл: {SOURCE_FILE}")
    source_hash = sha256(SOURCE_FILE)
    with sqlite3.connect(QUARANTINE_DB) as source_conn:
        source_records_count = source_conn.execute(
            "SELECT COUNT(*) FROM tbot_parking_import"
        ).fetchone()[0]

    backup = backup_database()
    conn = sqlite3.connect(MAIN_DB)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        ensure_schema(cur)
        apartment_id = validate_main(cur, rows)
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
                "vehicles", SOURCE_FILE.name, str(SOURCE_FILE), source_hash, PIPELINE,
                source_records_count, len(rows), timestamp, timestamp, CREATED_BY,
                "Первая контролируемая партия: только кв. 170, по согласованию с оператором.",
            ),
        )
        batch_id = cur.lastrowid
        vehicle_ids = []
        for row in rows:
            notes = (
                f"Добавлено из {SOURCE_FILE.name} → карантин; "
                f"партия #{batch_id}; строка quarantine #{row['id']}; {timestamp}."
            )
            cur.execute(
                """
                INSERT INTO vehicles(
                    apartment_id, license_plate, license_plate_normalized,
                    plate_format_status, car_model, car_model_normalized,
                    car_color, car_color_normalized, status, source, notes,
                    created_at, created_by, lifecycle_status, review_status, created_source
                ) VALUES (?, ?, ?, 'STANDARD', ?, ?, ?, ?, 'active', 'tbot_parking',
                          ?, ?, ?, 'ACTIVE', 'PENDING_RESIDENT_CONFIRMATION', ?)
                """,
                (
                    apartment_id, row["license_plate"], row["license_plate_normalized"],
                    row["car_model"], row["car_model_normalized"], row["car_color"],
                    row["car_color_normalized"], notes, timestamp, CREATED_BY,
                    f"{SOURCE_FILE.name} → quarantine (batch #{batch_id})",
                ),
            )
            vehicle_id = cur.lastrowid
            vehicle_ids.append(vehicle_id)
            payload = {key: row[key] for key in row.keys()}
            cur.execute(
                """
                INSERT INTO vehicle_import_batch_items(
                    batch_id, source_db, source_table, source_record_id,
                    apartment_number, license_plate_raw, license_plate_normalized,
                    source_payload_json, decision, decision_reason, vehicle_id, created_at
                ) VALUES (?, ?, 'tbot_parking_import', ?, ?, ?, ?, ?, 'APPLIED', ?, ?, ?)
                """,
                (
                    batch_id, str(QUARANTINE_DB), str(row["id"]), row["apartment_number"],
                    row["license_plate"], row["license_plate_normalized"],
                    json.dumps(payload, ensure_ascii=False),
                    "Оператор согласовал первую проверочную партию для кв. 170.", vehicle_id, timestamp,
                ),
            )
            cur.execute(
                """
                INSERT INTO audit_log(
                    event_time, username, table_name, record_id, action, field_name,
                    old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id
                ) VALUES (?, 'system', 'vehicles', ?, 'insert', '*', '', ?, ?, 'system', ?, ?, NULL)
                """,
                (
                    timestamp, str(vehicle_id),
                    json.dumps({"apartment": "170", "plate": row["license_plate_normalized"]}, ensure_ascii=False),
                    notes, CREATED_BY, SOURCE_FILE.name,
                ),
            )
        cur.execute(
            """
            UPDATE data_import_batches
            SET inserted_records_count=?, status='APPLIED'
            WHERE id=?
            """,
            (len(vehicle_ids), batch_id),
        )
        conn.commit()
        return batch_id, vehicle_ids
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dry_run() -> None:
    rows = load_source_rows()
    conn = sqlite3.connect(MAIN_DB)
    try:
        cur = conn.cursor()
        apartment_id = validate_main(cur, rows)
    finally:
        conn.close()
    print("DRY RUN ONLY — main DB will not change.")
    print(f"Apartment: 170 (id={apartment_id})")
    for row in rows:
        print(f"quarantine #{row['id']}: {row['license_plate_normalized']} | {row['car_model']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="First traceable TBot import batch for apartment 170.")
    parser.add_argument("--apply", action="store_true", help="Write the batch and two vehicles to the main DB.")
    args = parser.parse_args()
    if not args.apply:
        dry_run()
        return 0
    batch_id, vehicle_ids = apply()
    print(f"APPLIED batch #{batch_id}; vehicles: {', '.join(map(str, vehicle_ids))}")
    print("A backup was created in Data/db/backups.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
