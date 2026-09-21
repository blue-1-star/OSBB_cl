#!/usr/bin/env python3
"""Add effective parking-end fields to vehicles.

Run without --apply for a dry run.  The fields retain the resident-confirmed
last chargeable day and reason after the vehicle is archived; archive timestamp
alone is not suitable because a resident may report the sale retrospectively.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUPS = ROOT / "Data" / "db" / "backups"
MIGRATION = "2026-09-21_015_vehicle_parking_end_fields"


def backup_database(source: Path) -> Path:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    target = BACKUPS / f"before_{MIGRATION}_{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(source, target)
    return target


def missing_columns(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    present = {row[1] for row in conn.execute("PRAGMA table_info(vehicles)")}
    required = [
        ("parking_end_date", "TEXT"),
        ("parking_end_reason", "TEXT"),
        ("parking_end_recorded_at", "TEXT"),
        ("parking_end_financial_status", "TEXT"),
    ]
    return [(name, definition) for name, definition in required if name not in present]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")
    with sqlite3.connect(db) as conn:
        missing = missing_columns(conn)
    print(f"DB: {db}")
    print("Columns to add:", ", ".join(name for name, _ in missing) or "none")
    if not args.apply:
        print("DRY RUN ONLY — no changes saved. Use --apply to apply migration.")
        return 0
    if not missing:
        print("Already applied — no database change.")
        return 0

    backup = backup_database(db)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        for name, definition in missing_columns(conn):
            cur.execute(f"ALTER TABLE vehicles ADD COLUMN {name} {definition}")
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'system', 'vehicles', 'schema', 'migration', ?, '', ?, ?,
                    'system', 'migration', ?, NULL)
            """,
            (timestamp, ",".join(name for name, _ in missing), MIGRATION,
             "Добавлены effective-dated поля прекращения парковки.", MIGRATION),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"APPLIED. Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
