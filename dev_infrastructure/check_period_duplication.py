"""
Проверка: дублируется ли period_code (и вообще какие поля) между
payments и cashbox_operations — нужно понять, придётся ли при правке
периода платежа синхронно чинить и кассовую операцию, или это разные,
несвязанные записи.

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

    for table in ("payments", "cashbox_operations"):
        print(f"=== {table} ===")
        cols = cur.execute(f"PRAGMA table_info({table})").fetchall()
        if not cols:
            print("  (таблицы нет)")
            continue
        for c in cols:
            marker = " <-- period_code" if c[1] == "period_code" else ""
            print(f"  {c[1]} ({c[2]}){marker}")
        print()

    # Если у обеих таблиц есть period_code — проверим на конкретных
    # платежах, совпадает ли значение с их кассовыми операциями (если
    # вообще есть прямая связь между строками).
    pay_cols = {c[1] for c in cur.execute("PRAGMA table_info(payments)")}
    cbo_cols = {c[1] for c in cur.execute("PRAGMA table_info(cashbox_operations)")}

    if "period_code" in pay_cols and "period_code" in cbo_cols:
        print("⚠ period_code есть в ОБЕИХ таблицах — при правке периода платежа,")
        print("  скорее всего, нужно синхронно поправить и cashbox_operations.")
    elif "period_code" in cbo_cols and "period_code" not in pay_cols:
        print("period_code есть только в cashbox_operations, не в payments.")
    elif "period_code" in pay_cols and "period_code" not in cbo_cols:
        print("✅ period_code есть только в payments — cashbox_operations эту")
        print("  информацию не дублирует, править нужно только в одном месте.")
    else:
        print("period_code не найден ни в одной из таблиц.")

    conn.close()


if __name__ == "__main__":
    main()