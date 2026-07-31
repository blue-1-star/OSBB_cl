"""
Проверка: как связаны между собой payments и cashbox_operations —
нужно найти столбец-ссылку, прежде чем строить инструмент синхронной
правки period_code в обеих таблицах.

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

    pay_cols = [c[1] for c in cur.execute("PRAGMA table_info(payments)")]
    cbo_cols = [c[1] for c in cur.execute("PRAGMA table_info(cashbox_operations)")]

    print("Колонки payments, содержащие 'id' (кандидаты на связь):")
    for c in pay_cols:
        if "id" in c.lower():
            print(f"  {c}")
    print()

    print("Колонки cashbox_operations, содержащие 'id' (кандидаты на связь):")
    for c in cbo_cols:
        if "id" in c.lower():
            print(f"  {c}")
    print()

    # Самые вероятные имена связи — проверим напрямую, если такие колонки есть.
    candidates = ["payment_id", "cashbox_operation_id", "operation_id"]
    for col in candidates:
        if col in cbo_cols:
            print(f"✅ cashbox_operations.{col} существует — вероятная прямая ссылка на payments.id")
        if col in pay_cols:
            print(f"✅ payments.{col} существует — вероятная прямая ссылка на cashbox_operations.id")

    # Возьмём один реальный платёж и попробуем найти по нему операцию,
    # если связь нашлась выше.
    row = cur.execute("SELECT id, apartment_number, amount, period_code FROM payments ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        print(f"\nПример последнего платежа: id={row[0]}, кв.{row[1]}, {row[2]} грн, период {row[3]}")

    conn.close()


if __name__ == "__main__":
    main()