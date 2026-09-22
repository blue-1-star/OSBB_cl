#!/usr/bin/env python3
"""Add the generic, additive order-fulfillment model to the OSBB database."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from order_fulfillment_core import ensure_fulfillment_schema


DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUPS = ROOT / "Data" / "db" / "backups"
MIGRATION = "2026-09-22_018_order_fulfillment_model"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")
    with sqlite3.connect(db) as conn:
        present = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'order_fulfillment%'"
            )
        }
    print(f"DB: {db}")
    print("Existing generic fulfillment tables:", ", ".join(sorted(present)) or "none")
    if not args.apply:
        print("DRY RUN ONLY — no changes saved.")
        return 0

    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"before_{MIGRATION}_{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(db, backup)
    with sqlite3.connect(db) as conn:
        ensure_fulfillment_schema(conn)
        conn.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source)
            VALUES (?, 'system', 'order_fulfillments', 'schema', 'migration', '*', '', ?, ?,
                    'system', 'migration', ?)
            """,
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), MIGRATION,
             "Добавлена общая модель исполнения заказа: предмет, доступ или работа.", MIGRATION),
        )
        conn.commit()
    print(f"APPLIED. Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
