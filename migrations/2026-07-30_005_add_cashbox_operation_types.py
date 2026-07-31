"""
Миграция: справочник допустимых operation_type для cashbox_operations
Номер: 2026-07-30_005

Назначение: operation_type сейчас — обычный TEXT без CHECK и без
справочной таблицы, то есть никто не управляет набором допустимых
значений. Заводим лёгкий, не блокирующий справочник (документирует,
что уже реально используется + добавляет salary_payout для выдач) —
не жёсткое ограничение схемы, просто осознанная регистрация новых
типов операций через query_lib, а не случайная строка на ходу.
"""

import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import paths, USE_TEST_DB


def get_db_path():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def table_exists(db_path, table) -> bool:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    conn.close()
    return row is not None


def up():
    db_path = get_db_path()
    print(f"🔧 Миграция 2026-07-30_005: справочник cashbox_operation_types")
    print(f"📁 БД: {db_path}")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    if not table_exists(db_path, "cashbox_operation_types"):
        print("  ➜ Создаю таблицу cashbox_operation_types...")
        cur.execute("""
            CREATE TABLE cashbox_operation_types (
                operation_type TEXT PRIMARY KEY,
                expected_direction TEXT NOT NULL,
                description TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    else:
        print("  ⏭ Таблица уже существует.")

    seed = [
        ("cash_receipt", "in", "Приём наличных за услуги (парковка и т.п.)"),
        ("historical_income", "in", "Исторический приход при переносе данных"),
        ("historical_expense", "out", "Историческая правка расхода при переносе данных"),
        ("salary_payout", "out", "Выдача зарплаты (охранники и т.п.)"),
    ]
    for op_type, direction, desc in seed:
        cur.execute("SELECT 1 FROM cashbox_operation_types WHERE operation_type=?", (op_type,))
        if cur.fetchone():
            print(f"  ⏭ {op_type} уже зарегистрирован.")
            continue
        cur.execute(
            "INSERT INTO cashbox_operation_types (operation_type, expected_direction, description) VALUES (?, ?, ?)",
            (op_type, direction, desc),
        )
        print(f"  ➜ Зарегистрирован: {op_type} / {direction}")
    conn.commit()
    conn.close()
    print("✅ Готово.")


if __name__ == "__main__":
    up()
