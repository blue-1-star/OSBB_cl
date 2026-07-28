"""
Миграция: добавляем concerns_field в verification_journal
Номер: 2026-07-28_004
"""

import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import paths, USE_TEST_DB


def get_db_path():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def column_exists(db_path, table, column) -> bool:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = [c[1] for c in cur.fetchall()]
    conn.close()
    return column in cols


def up():
    db_path = get_db_path()
    print(f"🔧 Миграция 2026-07-28_004: добавляем concerns_field в verification_journal")
    print(f"📁 БД: {db_path}")

    if column_exists(db_path, 'verification_journal', 'concerns_field'):
        print("⏭ Колонка уже есть. Ничего не делаю.")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE verification_journal ADD COLUMN concerns_field TEXT")
        conn.commit()
        print("✅ Готово: concerns_field добавлена.")
    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка миграции, откат: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    up()
