#!/usr/bin/env python
"""
Миграция: Создание таблицы vehicle_candidates

Номер: 2026-07-14_001
Описание: Создаёт таблицу для хранения кандидатов в автомобили
и добавляет candidate_id в payments.
"""

import sys
import sqlite3
from pathlib import Path

# Добавляем пути
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import paths, USE_TEST_DB


def get_db_path():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def table_exists(cur, name):
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def column_exists(cur, table, column):
    cur.execute(f"PRAGMA table_info({table})")
    return column in [row[1] for row in cur.fetchall()]


def up():
    """Применяет миграцию"""
    db_path = get_db_path()
    print(f"🔧 Миграция 2026-07-14_001 на БД: {db_path}")
    
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    
    try:
        # 1. Создаём таблицу
        if not table_exists(cur, 'vehicle_candidates'):
            print("  ➜ Создаю таблицу vehicle_candidates...")
            cur.execute("""
                CREATE TABLE vehicle_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    license_plate TEXT NOT NULL,
                    license_plate_normalized TEXT NOT NULL,
                    car_model TEXT,
                    car_model_normalized TEXT,
                    apartment_id INTEGER,
                    apartment_number TEXT,
                    status TEXT DEFAULT 'PENDING',
                    resolved_vehicle_id INTEGER,
                    merged_vehicle_id INTEGER,
                    created_by INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT,
                    comment TEXT,
                    source TEXT DEFAULT 'cashier',
                    FOREIGN KEY (apartment_id) REFERENCES apartments(id),
                    FOREIGN KEY (resolved_vehicle_id) REFERENCES vehicles(id),
                    FOREIGN KEY (merged_vehicle_id) REFERENCES vehicles(id)
                )
            """)
            cur.execute("CREATE INDEX idx_candidates_plate ON vehicle_candidates(license_plate_normalized)")
            cur.execute("CREATE INDEX idx_candidates_status ON vehicle_candidates(status)")
            cur.execute("CREATE INDEX idx_candidates_apartment ON vehicle_candidates(apartment_id)")
            print("    ✅ Таблица и индексы созданы")
        else:
            print("  ⏭ Таблица уже существует, пропускаю")
        
        # 2. Добавляем колонку в payments
        if table_exists(cur, 'payments') and not column_exists(cur, 'payments', 'candidate_id'):
            print("  ➜ Добавляю candidate_id в payments...")
            cur.execute("ALTER TABLE payments ADD COLUMN candidate_id INTEGER")
            print("    ✅ Колонка добавлена")
        else:
            print("  ⏭ Колонка уже существует, пропускаю")
        
        conn.commit()
        print("✅ Миграция выполнена успешно!")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка: {e}")
        raise
    finally:
        conn.close()


def down():
    """Откат миграции (опасно!)"""
    print("⚠️ Откат не реализован для этой миграции")
    print("   (удаление таблицы и колонки — деструктивная операция)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--down', action='store_true', help='Откатить миграцию')
    args = parser.parse_args()
    
    if args.down:
        down()
    else:
        up()