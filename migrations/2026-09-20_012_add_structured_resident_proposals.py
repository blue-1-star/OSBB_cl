#!/usr/bin/env python3
"""Add a structured payload to the shared resident/operator task queue.

Existing tasks retain their readable ``description``. New self-service
proposals additionally store a JSON snapshot of current and proposed values,
which lets the future Streamlit operator workspace render the same proposal
without parsing a human message. Dry-run is the default; ``--apply`` creates
a SQLite backup first.
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


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def has_table(cur: sqlite3.Cursor, table: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def has_column(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    return column in {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}


def backup_database(source: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"before_2026-09-20_012_structured_resident_proposals_{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(source, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Add payload_json to operator_task_queue.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")

    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        if not has_table(cur, "operator_task_queue"):
            raise SystemExit("operator_task_queue is missing; run the onboarding schema first.")
        already_exists = has_column(cur, "operator_task_queue", "payload_json")
        print(f"DB: {db}")
        print(f"operator_task_queue.payload_json: {'EXISTS' if already_exists else 'MISSING'}")
        if not args.apply:
            print("DRY RUN ONLY — no changes saved. Use --apply to add the column.")
            return 0
        if already_exists:
            print("No schema change is needed.")
            return 0

        backup = backup_database(db)
        cur.execute("BEGIN IMMEDIATE")
        cur.execute("ALTER TABLE operator_task_queue ADD COLUMN payload_json TEXT")
        if has_table(cur, "audit_log"):
            cur.execute(
                """
                INSERT INTO audit_log(
                    event_time, username, table_name, record_id, action, field_name,
                    old_value, new_value, comment, actor_role, actor_name, source, telegram_user_id
                ) VALUES (?, 'system', 'operator_task_queue', 'schema', 'migration', 'payload_json',
                          '', 'TEXT', ?, 'system', 'migration/2026-09-20_012', 'migration', NULL)
                """,
                (now(), "Добавлено структурированное предложение жителя: current → proposed."),
            )
        conn.commit()
        print(f"APPLIED. Backup: {backup}")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
