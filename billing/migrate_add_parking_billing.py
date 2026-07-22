from pathlib import Path
import sys
import sqlite3
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def migrate():
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS parking_tariffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        parking_time TEXT UNIQUE,
        tariff_name TEXT,
        monthly_amount REAL DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        created_at TEXT
    )
    """)

    cur.execute("""
    INSERT OR IGNORE INTO parking_tariffs
        (parking_time, tariff_name, monthly_amount, created_at)
    VALUES
        ('Day', 'Day parking', 0, ?),
        ('Night', 'Night parking', 0, ?)
    """, (now(), now()))

    cur.execute("""
    CREATE TABLE IF NOT EXISTS billing_periods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period_name TEXT,
        date_from TEXT,
        date_to TEXT,
        months_count INTEGER DEFAULT 1,
        status TEXT DEFAULT 'draft',
        created_at TEXT,
        created_by TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS parking_charges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        billing_period_id INTEGER,
        apartment_id INTEGER,
        apartment_number TEXT,

        vehicle_id INTEGER,
        license_plate TEXT,
        license_plate_normalized TEXT,
        car_model TEXT,

        parking_time TEXT,
        months_count INTEGER DEFAULT 1,
        monthly_amount REAL DEFAULT 0,
        charged_amount REAL DEFAULT 0,

        paid_amount REAL DEFAULT 0,
        balance_amount REAL DEFAULT 0,

        payment_status TEXT DEFAULT 'unpaid',
        comment TEXT,

        created_at TEXT,
        updated_at TEXT,

        FOREIGN KEY(billing_period_id) REFERENCES billing_periods(id),
        FOREIGN KEY(apartment_id) REFERENCES apartments(id),
        FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS parking_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        parking_charge_id INTEGER,
        billing_period_id INTEGER,

        apartment_number TEXT,
        vehicle_id INTEGER,
        license_plate_normalized TEXT,

        payment_date TEXT,
        amount REAL,

        payment_method TEXT,
        cashier_name TEXT,
        cashbox_name TEXT,

        entered_by TEXT,
        entered_at TEXT,

        comment TEXT,

        FOREIGN KEY(parking_charge_id) REFERENCES parking_charges(id),
        FOREIGN KEY(billing_period_id) REFERENCES billing_periods(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cashboxes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cashbox_name TEXT UNIQUE,
        cashbox_type TEXT,
        entrance TEXT,
        responsible_person TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cash_transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        from_cashbox TEXT,
        to_cashbox TEXT,
        collector_name TEXT,

        transfer_date TEXT,
        amount REAL,

        comment TEXT,
        created_at TEXT,
        created_by TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_parking_charges_period
    ON parking_charges(billing_period_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_parking_charges_apartment
    ON parking_charges(apartment_number)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_parking_charges_status
    ON parking_charges(payment_status)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_parking_payments_charge
    ON parking_payments(parking_charge_id)
    """)

    conn.commit()
    conn.close()

    print("Parking billing tables created successfully")
    print("DB:", paths.OSBB_DB_FILE)


if __name__ == "__main__":
    migrate()