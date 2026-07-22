"""
Исправляет только устаревшие программные заметки у уже подтверждённых помещений.

Меняет ТОЛЬКО записи:
  record_status = CONFIRMED
  internal_note начинается с:
  "Создано программно как черновик."

На:
  "Существование и официальный номер подтверждены оператором.
   Требуется уточнить площадь, контакты и договорные условия."

Каждое изменение пишет отдельную запись в operator_audit_log.

По умолчанию dry-run.
Запуск:
  g:/Programming/Py/venv/Scripts/python.exe G:/Programming/Py/OSBB/repair_confirmed_unit_seed_notes.py
  g:/Programming/Py/venv/Scripts/python.exe G:/Programming/Py/OSBB/repair_confirmed_unit_seed_notes.py --apply
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent
for folder in (ROOT, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB
from audit_logger import audit_field_change


OLD_PREFIX = "Создано программно как черновик."
NEW_NOTE = (
    "Существование и официальный номер подтверждены оператором. "
    "Требуется уточнить площадь, контакты и договорные условия."
)


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = get_db_file()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            unit_code,
            apartment_number,
            internal_note
        FROM apartments
        WHERE record_status = 'CONFIRMED'
          AND COALESCE(internal_note, '') LIKE ?
        ORDER BY id
    """, (OLD_PREFIX + "%",))
    rows = [dict(row) for row in cur.fetchall()]

    print("=" * 100)
    print("REPAIR CONFIRMED UNIT SEED NOTES")
    print("=" * 100)
    print("DB:", db)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", args.apply)
    print("Candidates:", len(rows))

    if not rows:
        print("Нет записей для исправления.")
        conn.close()
        return

    for row in rows:
        code = row.get("unit_code") or row.get("apartment_number") or row["id"]
        print(f"  {code}: {row['internal_note']!r}")

    if not args.apply:
        conn.rollback()
        conn.close()
        print()
        print("DRY RUN COMPLETED - NO CHANGES SAVED")
        return

    audit_ids = []
    for row in rows:
        cur.execute("""
            UPDATE apartments
            SET internal_note = ?, unit_updated_at = ?
            WHERE id = ?
        """, (NEW_NOTE, now_db(), int(row["id"])))

        audit_id = audit_field_change(
            conn=conn,
            table_name="apartments",
            row_id=int(row["id"]),
            field_name="internal_note",
            old_value=row["internal_note"],
            new_value=NEW_NOTE,
            operator_id="system",
            user_id="system",
            actor_type="system",
            action_type="unit_seed_note_repaired",
            source_context="repair_confirmed_unit_seed_notes.py",
            comment=(
                "Устаревшая программная заметка черновика заменена "
                "после подтверждения помещения."
            ),
            extra={
                "unit_code": row.get("unit_code") or row.get("apartment_number"),
            },
        )
        audit_ids.append(int(audit_id))

    conn.commit()
    conn.close()

    print()
    print("APPLIED")
    print("Audit IDs:", ", ".join(map(str, audit_ids)))


if __name__ == "__main__":
    main()
