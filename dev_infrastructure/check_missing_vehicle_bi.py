"""
Быстрая проверка: куда реально попала (если попала) запись об авто
BI0230HT, введённая через "➕ Добавить автомобиль", если её не видно
в vehicles.

Только читает.
"""

import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import paths, USE_TEST_DB


def get_db_path():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def main():
    db_path = get_db_path()
    print(f"📁 БД: {db_path}\n")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Все таблицы, в названии которых есть "vehicle" или "candidate"
    tables = cur.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND (name LIKE '%vehicle%' OR name LIKE '%candidate%')
    """).fetchall()
    print("Таблицы для проверки:", [t[0] for t in tables], "\n")

    for (table,) in tables:
        cols = [c[1] for c in cur.execute(f"PRAGMA table_info({table})")]
        plate_cols = [c for c in cols if 'plate' in c.lower() or 'номер' in c.lower()]
        if not plate_cols:
            continue
        for col in plate_cols:
            try:
                rows = cur.execute(
                    f"SELECT * FROM {table} WHERE UPPER(REPLACE(REPLACE({col}, ' ', ''), '-', '')) LIKE '%BI0230HT%'"
                ).fetchall()
                if rows:
                    print(f"=== Найдено в {table}.{col} ===")
                    names = [d[0] for d in cur.description]
                    for r in rows:
                        print(dict(zip(names, r)))
            except sqlite3.OperationalError:
                pass

    print("\nЕсли ничего не найдено выше — записи нет ни в одной из проверенных таблиц.")
    conn.close()


if __name__ == "__main__":
    main()