"""
Проверка: есть ли уже готовый справочник допустимых operation_type для
cashbox_operations (по аналогии с service_catalog для услуг), прежде
чем предлагать заводить новый.

Ничего не меняет, только читает и печатает.
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

    print("=== Таблицы, в названии которых есть 'operation' или 'type' ===")
    tables = cur.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND (name LIKE '%operation%' OR name LIKE '%_type%' OR name LIKE '%catalog%')
    """).fetchall()
    for t in tables:
        print(f"  {t[0]}")
    print()

    print("=== Есть ли CHECK-ограничение на cashbox_operations.operation_type ===")
    schema = cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='cashbox_operations'").fetchone()
    if schema and 'CHECK' in schema[0].upper() and 'operation_type' in schema[0]:
        print("  Похоже, есть CHECK — смотрите полный SQL ниже.")
    else:
        print("  CHECK-ограничения на operation_type не найдено.")
    print()

    print("=== Все реально используемые сочетания (уже видели, для полноты) ===")
    rows = cur.execute("SELECT operation_type, direction, COUNT(*) FROM cashbox_operations GROUP BY operation_type, direction").fetchall()
    for r in rows:
        print(f"  {r[0]} / {r[1]}: {r[2]}")

    conn.close()


if __name__ == "__main__":
    main()