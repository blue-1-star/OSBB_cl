from pathlib import Path
import sys
import sqlite3

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths, USE_TEST_DB


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def main():
    paths.ensure_directories()

    db_file = get_db_file()
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    print("=" * 70)
    print("BILLING CORE MIGRATION")
    print("=" * 70)
    print("DB:", db_file)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS service_catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service_code TEXT NOT NULL UNIQUE,
        service_group TEXT NOT NULL,
        service_name TEXT NOT NULL,
        unit TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        comment TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS service_tariffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        service_code TEXT NOT NULL,
        amount REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'UAH',
        valid_from TEXT NOT NULL,
        valid_to TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        comment TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(service_code)
            REFERENCES service_catalog(service_code)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS charges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period_code TEXT NOT NULL,
        apartment_number TEXT NOT NULL,
        vehicle_id INTEGER,
        service_code TEXT NOT NULL,
        quantity REAL NOT NULL DEFAULT 1,
        unit_price REAL NOT NULL,
        amount REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'UAH',
        status TEXT NOT NULL DEFAULT 'unpaid',
        source TEXT,
        created_by TEXT,
        comment TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(vehicle_id)
            REFERENCES vehicles(id),
        FOREIGN KEY(service_code)
            REFERENCES service_catalog(service_code)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payment_date TEXT,
        period_code TEXT,
        apartment_number TEXT NOT NULL,
        vehicle_id INTEGER,
        amount REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'UAH',
        payment_method TEXT,
        source TEXT,
        created_by TEXT,
        comment TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(vehicle_id)
            REFERENCES vehicles(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_allocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payment_id INTEGER NOT NULL,
        charge_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(payment_id)
            REFERENCES payments(id),
        FOREIGN KEY(charge_id)
            REFERENCES charges(id)
    )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_service_tariffs_code_dates ON service_tariffs(service_code, valid_from, valid_to)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_charges_period ON charges(period_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_charges_apartment ON charges(apartment_number)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_charges_vehicle ON charges(vehicle_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_charges_service ON charges(service_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_charges_status ON charges(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_period ON payments(period_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_apartment ON payments(apartment_number)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_vehicle ON payments(vehicle_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(payment_date)")

    services = [
        ("PARKING_DAY", "parking", "Парковка: дневной тариф", "month", "Обычный тариф Day для собственников"),
        ("PARKING_NIGHT", "parking", "Парковка: суточный тариф", "month", "Обычный тариф Night для собственников"),
        ("GUEST_NIGHT", "guest", "Гость с ночёвкой", "night", "Гостевая ночная парковка"),
        ("BARRIER_CALL", "barrier", "Открытие шлагбаума телефонным звонком", "call", "Дополнительный сервис"),
    ]

    cur.executemany("""
        INSERT OR IGNORE INTO service_catalog (
            service_code,
            service_group,
            service_name,
            unit,
            comment
        )
        VALUES (?, ?, ?, ?, ?)
    """, services)

    cur.execute("""
        INSERT INTO service_tariffs (
            service_code,
            amount,
            currency,
            valid_from,
            valid_to,
            is_active,
            comment
        )
        SELECT
            'GUEST_NIGHT',
            50,
            'UAH',
            '2026-01-01',
            NULL,
            1,
            'Initial guest night tariff'
        WHERE NOT EXISTS (
            SELECT 1
            FROM service_tariffs
            WHERE service_code = 'GUEST_NIGHT'
              AND valid_to IS NULL
        )
    """)

    conn.commit()

    for table_name in [
        "service_catalog",
        "service_tariffs",
        "charges",
        "payments",
        "payment_allocations",
    ]:
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cur.fetchone()[0]
        print(f"{table_name}: {count}")

    conn.close()

    print("")
    print("=" * 70)
    print("MIGRATION COMPLETED")
    print("=" * 70)


if __name__ == "__main__":
    main()
