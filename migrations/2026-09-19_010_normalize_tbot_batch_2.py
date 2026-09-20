#!/usr/bin/env python3
"""Repair missing model/color normalizations in the already applied TBot batch #2."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import normalize_car_model, normalize_color

MAIN_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUP_DIR = ROOT / "Data" / "db" / "backups"
BATCH_ID = 2
CREATED_BY = "migration/2026-09-19_010"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT v.id, v.car_model, v.car_model_normalized, v.car_color, v.car_color_normalized
        FROM vehicles v
        JOIN vehicle_import_batch_items i ON i.vehicle_id=v.id
        WHERE i.batch_id=? AND i.decision='APPLIED'
        ORDER BY v.id
        """,
        (BATCH_ID,),
    ).fetchall()
    if len(rows) != 18:
        raise RuntimeError(f"Ожидалось 18 автомобилей партии #{BATCH_ID}, найдено {len(rows)}.")
    return rows


def plan(rows: list[sqlite3.Row]) -> list[dict]:
    result = []
    for row in rows:
        model = normalize_car_model(row["car_model"])
        color = normalize_color(row["car_color"])
        if row["car_model_normalized"] != model or row["car_color_normalized"] != color:
            result.append(
                {
                    "id": row["id"],
                    "old_model": row["car_model_normalized"],
                    "new_model": model,
                    "old_color": row["car_color_normalized"],
                    "new_color": color,
                }
            )
    return result


def backup_database() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"before_2026-09-19_010_normalize_tbot_batch_2_{datetime.now():%Y%m%d-%H%M%S}.db"
    source = sqlite3.connect(MAIN_DB)
    backup = sqlite3.connect(target)
    try:
        source.backup(backup)
    finally:
        backup.close()
        source.close()
    return target


def apply(changes: list[dict]) -> Path:
    backup = backup_database()
    conn = sqlite3.connect(MAIN_DB)
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        timestamp = now()
        for change in changes:
            cur.execute(
                """
                UPDATE vehicles
                SET car_model_normalized=?, car_color_normalized=?,
                    updated_at=?, updated_by=?
                WHERE id=?
                """,
                (change["new_model"], change["new_color"], timestamp, CREATED_BY, change["id"]),
            )
            cur.execute(
                """
                INSERT INTO audit_log(
                    event_time, username, table_name, record_id, action, field_name,
                    old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id
                ) VALUES (?, 'system', 'vehicles', ?, 'normalize_import_fields',
                          'car_model_normalized,car_color_normalized', ?, ?, ?,
                          'system', ?, 'parking_tbot2.xlsx', NULL)
                """,
                (
                    timestamp,
                    str(change["id"]),
                    json.dumps({"model": change["old_model"], "color": change["old_color"]}, ensure_ascii=False),
                    json.dumps({"model": change["new_model"], "color": change["new_color"]}, ensure_ascii=False),
                    "Исправление: в партии №2 пропущен вызов нормализаторов модели и цвета.",
                    CREATED_BY,
                ),
            )
        conn.commit()
        return backup
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize model and color for applied TBot batch #2.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with sqlite3.connect(MAIN_DB) as conn:
        changes = plan(load_rows(conn))
    print(f"Vehicles in batch #{BATCH_ID}: 18; rows to normalize: {len(changes)}")
    for item in changes:
        print(f"vehicle #{item['id']}: model {item['old_model']!r} -> {item['new_model']!r}; color {item['old_color']!r} -> {item['new_color']!r}")
    if not args.apply:
        print("DRY RUN ONLY — main DB will not change.")
        return 0
    backup = apply(changes)
    print(f"APPLIED. Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
