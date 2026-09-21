#!/usr/bin/env python3
"""Queue a Telegram result for every resolved resident self-service request."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUPS = ROOT / "Data" / "db" / "backups"
MIGRATION = "2026-09-21_017_resident_resolution_notifications"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(resident_request_messages)")}
        trigger_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='trg_resident_request_resolution_notification'"
        ).fetchone() is not None
    print(f"DB: {db}")
    print("message_kind column:", "already exists" if "message_kind" in columns else "will be added")
    print("resolution trigger:", "already exists" if trigger_exists else "will be created")
    if not args.apply:
        print("DRY RUN ONLY — no changes saved.")
        return 0
    if "message_kind" in columns and trigger_exists:
        print("Already applied — no database change.")
        return 0

    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"before_{MIGRATION}_{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(db, backup)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        if "message_kind" not in columns:
            cur.execute("ALTER TABLE resident_request_messages ADD COLUMN message_kind TEXT NOT NULL DEFAULT 'CLARIFICATION'")
        if not trigger_exists:
            cur.execute(
                """
                CREATE TRIGGER trg_resident_request_resolution_notification
                AFTER UPDATE OF status ON operator_task_queue
                WHEN OLD.status <> NEW.status
                 AND NEW.origin = 'RESIDENT_PORTAL'
                 AND NEW.status IN ('RESOLVED', 'REJECTED', 'CLOSED')
                 AND COALESCE(TRIM(NEW.telegram_user_id), '') <> ''
                BEGIN
                    INSERT INTO resident_request_messages(
                        request_id, telegram_user_id, direction, message_text, delivery_status,
                        created_by, created_at, message_kind
                    ) VALUES (
                        NEW.id, NEW.telegram_user_id, 'OPERATOR_TO_RESIDENT',
                        'Заявка №' || NEW.id || ' обработана. ' ||
                        COALESCE(NULLIF(TRIM(NEW.close_note), ''), 'Оператор завершил рассмотрение.'),
                        'READY', 'system/resolution-trigger', CURRENT_TIMESTAMP, 'RESOLUTION'
                    );
                END
                """
            )
        cur.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id)
            VALUES (?, 'system', 'resident_request_messages', 'schema', 'migration', '*', '', ?, ?,
                    'system', 'migration', ?, NULL)
            """,
            (timestamp, MIGRATION, "Добавлены автоматические уведомления жителю о решении заявки.", MIGRATION),
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
