#!/usr/bin/env python3
"""Create the auditable dialogue queue for resident self-service requests."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUPS = ROOT / "Data" / "db" / "backups"
MIGRATION = "2026-09-21_016_resident_request_messages"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")
    with sqlite3.connect(db) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resident_request_messages'"
        ).fetchone() is not None
    print(f"DB: {db}")
    print("resident_request_messages:", "already exists" if exists else "will be created")
    if not args.apply or exists:
        print("DRY RUN ONLY — no changes saved." if not args.apply else "Already applied — no database change.")
        return 0

    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"before_{MIGRATION}_{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(db, backup)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            """
            CREATE TABLE resident_request_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                telegram_user_id TEXT NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('OPERATOR_TO_RESIDENT', 'RESIDENT_TO_OPERATOR')),
                message_text TEXT NOT NULL,
                delivery_status TEXT NOT NULL DEFAULT 'READY'
                    CHECK(delivery_status IN ('READY', 'SENT', 'FAILED', 'READ')),
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT,
                read_at TEXT,
                delivery_error TEXT,
                FOREIGN KEY(request_id) REFERENCES operator_task_queue(id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX idx_resident_request_messages_request ON resident_request_messages(request_id, id)"
        )
        cur.execute(
            "CREATE INDEX idx_resident_request_messages_delivery ON resident_request_messages(delivery_status, direction, created_at)"
        )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'system', 'resident_request_messages', 'schema', 'migration', '*', '', ?, ?,
                    'system', 'migration', ?, NULL)
            """,
            (timestamp, MIGRATION, "Добавлена переписка оператора и жителя по self-service заявкам.", MIGRATION),
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
