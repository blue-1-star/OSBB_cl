#!/usr/bin/env python
"""
Миграция: разрешаем NULL в payments.apartment_number
Номер: 2026-07-15_002
"""

import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import paths, USE_TEST_DB


def get_db_path():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def column_has_not_null(db_path: str, table: str, column: str) -> bool:
    """Проверяет, есть ли NOT NULL у колонки"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    columns = cur.fetchall()
    conn.close()
    for col in columns:
        if col[1] == column:
            return bool(col[3])  # notnull = 1
    return False


def up():
    db_path = get_db_path()
    print(f"🔧 Миграция 2026-07-15_002: разрешаем NULL в payments.apartment_number")
    print(f"📁 БД: {db_path}")
    
    # Проверяем, нужно ли что-то делать
    if not column_has_not_null(db_path, 'payments', 'apartment_number'):
        print("⏭ Колонка уже допускает NULL. Ничего не делаю.")
        return
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    try:
        print("  ➜ Создаю временную таблицу...")
        cur.execute("""
            CREATE TABLE payments_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_date TEXT,
                period_code TEXT,
                apartment_number TEXT,
                vehicle_id INTEGER,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'UAH',
                payment_method TEXT,
                source TEXT,
                created_by TEXT,
                comment TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                cashbox_code TEXT,
                cashbox_operation_id INTEGER,
                cashier_batch_id TEXT,
                operator_id TEXT,
                service_item_code TEXT,
                base_service_code TEXT,
                service_type TEXT,
                commercial_contract_id INTEGER,
                commercial_unit_id INTEGER,
                cashier_receipt_id INTEGER,
                cashier_entry_status TEXT,
                payment_notice_id INTEGER,
                bank_transaction_id INTEGER,
                payment_channel TEXT,
                apartment_id INTEGER,
                source_ref TEXT,
                candidate_id INTEGER,
                FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
            )
        """)
        
        print("  ➜ Копирую данные...")
        cur.execute("INSERT INTO payments_new SELECT * FROM payments")
        
        print("  ➜ Удаляю старую таблицу...")
        cur.execute("DROP TABLE payments")
        
        print("  ➜ Переименовываю новую...")
        cur.execute("ALTER TABLE payments_new RENAME TO payments")
        
        conn.commit()
        print("✅ Миграция выполнена успешно!")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка: {e}")
        raise
    finally:
        conn.close()


def down():
    print("⚠️ Откат не реализован для этой миграции.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--down', action='store_true', help='Откатить миграцию')
    args = parser.parse_args()
    
    if args.down:
        down()
    else:
        up()