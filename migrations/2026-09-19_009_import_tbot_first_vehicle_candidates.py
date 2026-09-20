#!/usr/bin/env python3
"""Apply the approved second batch of first-vehicle TBot candidates.

Only rows currently classified as ``CANDIDATE_FOR_FIRST_IMPORT`` are selected:
their physical apartment exists and currently has no registered vehicles.  The
command is read-only without ``--apply``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dry_run_tbot_vehicle_reconciliation import classify_rows
from utils import normalize_car_model, normalize_color

MAIN_DB = ROOT / "Data" / "db" / "osbb_test.db"
QUARANTINE_DB = ROOT / "Data" / "db" / "osbb_quarantine.db"
SOURCE_FILE = ROOT / "Data" / "raw" / "typed" / "parking_tbot2.xlsx"
BACKUP_DIR = ROOT / "Data" / "db" / "backups"
PIPELINE = "parking_tbot2.xlsx → quarantine → main vehicles"
CREATED_BY = "migration/2026-09-19_009"
CATEGORY = "CANDIDATE_FOR_FIRST_IMPORT"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_candidates() -> list[dict]:
    with sqlite3.connect(MAIN_DB) as main, sqlite3.connect(QUARANTINE_DB) as quarantine:
        rows = classify_rows(main, quarantine)
    return [row for row in rows if row["category"] == CATEGORY]


def verify_schema(cur: sqlite3.Cursor) -> None:
    for table in ("data_import_batches", "vehicle_import_batch_items", "audit_log", "vehicles"):
        found = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not found:
            raise RuntimeError(f"Не найдена обязательная таблица {table}.")


def backup_database() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"before_2026-09-19_009_tbot_first_candidates_{datetime.now():%Y%m%d-%H%M%S}.db"
    source = sqlite3.connect(MAIN_DB)
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup


def apply(candidates: list[dict]) -> tuple[int, list[int], Path]:
    if not candidates:
        raise RuntimeError("Нет кандидатов для второй партии: база уже изменилась или отбор устарел.")
    if not SOURCE_FILE.exists():
        raise RuntimeError(f"Не найден исходный файл: {SOURCE_FILE}")
    source_hash = sha256(SOURCE_FILE)
    with sqlite3.connect(QUARANTINE_DB) as quarantine:
        source_records_count = quarantine.execute(
            "SELECT COUNT(*) FROM tbot_parking_import"
        ).fetchone()[0]

    backup = backup_database()
    conn = sqlite3.connect(MAIN_DB)
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        verify_schema(cur)
        # The selection criterion is "no cars before this batch". Several
        # legitimate candidate rows may belong to the same such apartment.
        for apartment_id in {item["target_apartment_id"] for item in candidates}:
            if cur.execute(
                "SELECT 1 FROM vehicles WHERE apartment_id=? LIMIT 1", (apartment_id,)
            ).fetchone():
                raise RuntimeError(
                    "Состав второй партии устарел: одна из квартир уже получила автомобиль. "
                    "Повторите сухой прогон."
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
                "vehicles", SOURCE_FILE.name, str(SOURCE_FILE), source_hash, PIPELINE,
                source_records_count, len(candidates), timestamp, timestamp, CREATED_BY,
                "Вторая согласованная партия: кандидаты первичного заполнения квартир без автомобилей.",
            ),
        )
        batch_id = cur.lastrowid
        vehicle_ids = []
        for item in candidates:
            duplicate = cur.execute(
                """
                SELECT id FROM vehicles
                WHERE UPPER(COALESCE(license_plate_normalized, license_plate))=?
                """,
                (item["license_plate_normalized"],),
            ).fetchone()
            if duplicate:
                raise RuntimeError(
                    f"Конфликт перед вставкой: {item['license_plate_normalized']} уже vehicles.id={duplicate[0]}."
                )
            notes = (
                f"Добавлено из {SOURCE_FILE.name} → карантин; "
                f"партия #{batch_id}; строка quarantine #{item['source_id']}; {timestamp}."
            )
            model_normalized = normalize_car_model(item["car_model"])
            color_normalized = normalize_color(item["car_color"])
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
                    item["target_apartment_id"], item["license_plate"], item["license_plate_normalized"],
                    item["car_model"], model_normalized, item["car_color"], color_normalized,
                    notes, timestamp, CREATED_BY,
                    f"{SOURCE_FILE.name} → quarantine (batch #{batch_id})",
                ),
            )
            vehicle_id = cur.lastrowid
            vehicle_ids.append(vehicle_id)
            payload = {
                key: item[key]
                for key in (
                    "source_id", "apartment_raw", "apartment_number", "full_name", "phone_raw",
                    "car_model", "car_color", "license_plate", "license_plate_normalized",
                    "status_raw", "ownership_type", "source", "imported_at",
                )
            }
            cur.execute(
                """
                INSERT INTO vehicle_import_batch_items(
                    batch_id, source_db, source_table, source_record_id,
                    apartment_number, license_plate_raw, license_plate_normalized,
                    source_payload_json, decision, decision_reason, vehicle_id, created_at
                ) VALUES (?, ?, 'tbot_parking_import', ?, ?, ?, ?, ?, 'APPLIED', ?, ?, ?)
                """,
                (
                    batch_id, str(QUARANTINE_DB), str(item["source_id"]), item["apartment_number"],
                    item["license_plate"], item["license_plate_normalized"],
                    json.dumps(payload, ensure_ascii=False),
                    "Оператор согласовал вторую партию кандидатов первичного заполнения.",
                    vehicle_id, timestamp,
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
                    json.dumps({"apartment": item["apartment_number"], "plate": item["license_plate_normalized"]}, ensure_ascii=False),
                    notes, CREATED_BY, SOURCE_FILE.name,
                ),
            )
        cur.execute(
            "UPDATE data_import_batches SET inserted_records_count=?, status='APPLIED' WHERE id=?",
            (len(vehicle_ids), batch_id),
        )
        conn.commit()
        return batch_id, vehicle_ids, backup
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply approved TBot first-vehicle candidates as one batch.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    candidates = select_candidates()
    print(f"Candidates selected ({CATEGORY}): {len(candidates)}")
    for item in candidates:
        print(f"quarantine #{item['source_id']}: кв.{item['apartment_number']} | {item['license_plate_normalized']} | {item['car_model'] or '-'}")
    if not args.apply:
        print("DRY RUN ONLY — main DB will not change.")
        return 0
    batch_id, vehicle_ids, backup = apply(candidates)
    print(f"APPLIED batch #{batch_id}; inserted vehicles: {len(vehicle_ids)} ({', '.join(map(str, vehicle_ids))})")
    print(f"Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
