from pathlib import Path
import sys
import sqlite3
import argparse
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths, USE_TEST_DB


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def table_exists(cur, table_name):
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def add_column_if_missing(cur, table_name, column_name, column_def):
    cols = table_columns(cur, table_name)
    if column_name not in cols:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
        return True
    return False


def ensure_operator_audit_log(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS operator_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            operator_id TEXT,
            user_id TEXT,
            actor_type TEXT DEFAULT 'system',
            action_type TEXT,
            table_name TEXT,
            row_id TEXT,
            field_name TEXT,
            old_value TEXT,
            new_value TEXT,
            action_status TEXT DEFAULT 'applied',
            review_status TEXT DEFAULT 'pending',
            reviewed_by TEXT,
            reviewed_at TEXT,
            review_comment TEXT,
            source_context TEXT,
            comment TEXT,
            extra_json TEXT
        )
    """)

    added = []
    migrations = {
        "created_at": "TEXT",
        "operator_id": "TEXT",
        "user_id": "TEXT",
        "actor_type": "TEXT DEFAULT 'system'",
        "action_type": "TEXT",
        "table_name": "TEXT",
        "row_id": "TEXT",
        "field_name": "TEXT",
        "old_value": "TEXT",
        "new_value": "TEXT",
        "action_status": "TEXT DEFAULT 'applied'",
        "review_status": "TEXT DEFAULT 'pending'",
        "reviewed_by": "TEXT",
        "reviewed_at": "TEXT",
        "review_comment": "TEXT",
        "source_context": "TEXT",
        "comment": "TEXT",
        "extra_json": "TEXT",
        "db_mode": "TEXT",
        "db_file": "TEXT",
    }

    for col, col_def in migrations.items():
        if add_column_if_missing(cur, "operator_audit_log", col, col_def):
            added.append(col)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_operator_audit_created ON operator_audit_log(created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_operator_audit_operator ON operator_audit_log(operator_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_operator_audit_actor ON operator_audit_log(actor_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_operator_audit_action ON operator_audit_log(action_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_operator_audit_review ON operator_audit_log(review_status)")

    return added


def create_cashboxes(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashboxes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cashbox_code TEXT NOT NULL UNIQUE,
            cashbox_name TEXT NOT NULL,
            currency TEXT NOT NULL DEFAULT 'UAH',
            initial_balance REAL NOT NULL DEFAULT 0,
            current_balance REAL NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cashboxes_code ON cashboxes(cashbox_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cashboxes_active ON cashboxes(is_active)")


def create_cashbox_operations(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashbox_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_date TEXT NOT NULL,
            cashbox_code TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'UAH',
            period_code TEXT,
            apartment_number TEXT,
            vehicle_id INTEGER,
            service_code TEXT,
            payment_id INTEGER,
            charge_id INTEGER,
            batch_id TEXT,
            source_type TEXT DEFAULT 'cashier_journal',
            source_ref TEXT,
            operator_id TEXT,
            actor_type TEXT DEFAULT 'operator',
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cashbox_code) REFERENCES cashboxes(cashbox_code),
            FOREIGN KEY (vehicle_id) REFERENCES vehicles(id),
            FOREIGN KEY (payment_id) REFERENCES payments(id),
            FOREIGN KEY (charge_id) REFERENCES charges(id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cashbox_operations_date ON cashbox_operations(operation_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cashbox_operations_cashbox ON cashbox_operations(cashbox_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cashbox_operations_apt ON cashbox_operations(apartment_number)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cashbox_operations_vehicle ON cashbox_operations(vehicle_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cashbox_operations_payment ON cashbox_operations(payment_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cashbox_operations_batch ON cashbox_operations(batch_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cashbox_operations_operator ON cashbox_operations(operator_id)")


def create_cashier_batches(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashier_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL UNIQUE,
            batch_type TEXT NOT NULL,
            period_code TEXT,
            cashbox_code TEXT,
            service_code TEXT,
            default_amount REAL,
            default_tariff TEXT,
            operator_id TEXT,
            actor_type TEXT DEFAULT 'operator',
            total_rows INTEGER DEFAULT 0,
            total_amount REAL DEFAULT 0,
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            applied_at TEXT,
            batch_status TEXT DEFAULT 'draft',
            FOREIGN KEY (cashbox_code) REFERENCES cashboxes(cashbox_code)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cashier_batches_batch_id ON cashier_batches(batch_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cashier_batches_status ON cashier_batches(batch_status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cashier_batches_operator ON cashier_batches(operator_id)")


def ensure_payments_cashbox_columns(cur):
    if not table_exists(cur, "payments"):
        return []

    added = []
    migrations = {
        "cashbox_code": "TEXT",
        "cashbox_operation_id": "INTEGER",
        "cashier_batch_id": "TEXT",
        "operator_id": "TEXT",
    }

    for col, col_def in migrations.items():
        if add_column_if_missing(cur, "payments", col, col_def):
            added.append(col)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_cashbox_code ON payments(cashbox_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_cashbox_operation ON payments(cashbox_operation_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_cashier_batch ON payments(cashier_batch_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_operator ON payments(operator_id)")

    return added


def seed_default_cashboxes(cur):
    defaults = [
        ("O", "Касса охраны", "UAH", 0, 0, 1, "Основная касса охранников"),
        ("K", "Касса консьержа", "UAH", 0, 0, 1, "Резервная касса консьержа"),
        ("BANK", "Банк / безнал", "UAH", 0, 0, 1, "Безналичные оплаты"),
    ]

    inserted = 0
    for code, name, currency, initial, current, active, comment in defaults:
        cur.execute("SELECT id FROM cashboxes WHERE cashbox_code = ?", (code,))
        if cur.fetchone():
            continue
        cur.execute("""
            INSERT INTO cashboxes (
                cashbox_code, cashbox_name, currency,
                initial_balance, current_balance, is_active,
                comment, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            code, name, currency, initial, current, active, comment,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
        inserted += 1
    return inserted


def recalc_cashbox_balance(cur, cashbox_code):
    cur.execute("SELECT initial_balance FROM cashboxes WHERE cashbox_code = ?", (cashbox_code,))
    row = cur.fetchone()
    if not row:
        return None

    initial = float(row[0] or 0)

    cur.execute("""
        SELECT COALESCE(SUM(
            CASE
                WHEN direction = 'in' THEN amount
                WHEN direction = 'out' THEN -amount
                ELSE 0
            END
        ), 0)
        FROM cashbox_operations
        WHERE cashbox_code = ?
    """, (cashbox_code,))
    delta = float(cur.fetchone()[0] or 0)
    balance = initial + delta

    cur.execute("""
        UPDATE cashboxes
        SET current_balance = ?, updated_at = ?
        WHERE cashbox_code = ?
    """, (balance, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), cashbox_code))
    return balance


def recalc_all_cashboxes(cur):
    cur.execute("SELECT cashbox_code FROM cashboxes")
    result = {}
    for (code,) in cur.fetchall():
        result[code] = recalc_cashbox_balance(cur, code)
    return result


def migrate(apply=True):
    db_file = get_db_file()
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    print("=" * 70)
    print("CASHIER CORE MIGRATION")
    print("=" * 70)
    print("DB:", db_file)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", apply)
    print("")

    audit_added = ensure_operator_audit_log(cur)
    create_cashboxes(cur)
    create_cashbox_operations(cur)
    create_cashier_batches(cur)
    payment_added = ensure_payments_cashbox_columns(cur)
    inserted_cashboxes = seed_default_cashboxes(cur)
    balances = recalc_all_cashboxes(cur)

    if apply:
        conn.commit()
    else:
        conn.rollback()

    def table_count(name):
        if not table_exists(cur, name):
            return "NO TABLE"
        cur.execute(f"SELECT COUNT(*) FROM {name}")
        return cur.fetchone()[0]

    print("Tables:")
    print("  cashboxes:", table_count("cashboxes"))
    print("  cashbox_operations:", table_count("cashbox_operations"))
    print("  cashier_batches:", table_count("cashier_batches"))
    print("  operator_audit_log:", table_count("operator_audit_log"))
    print("")
    print("Added audit columns:", ", ".join(audit_added) if audit_added else "none")
    print("Added payments columns:", ", ".join(payment_added) if payment_added else "none")
    print("Inserted default cashboxes:", inserted_cashboxes)
    print("")
    print("Cashbox balances:")
    if balances:
        for code, balance in balances.items():
            print(f"  {code}: {balance}")
    else:
        print("  none")
    print("")
    print("======================================================================")
    print("MIGRATION COMPLETED" if apply else "DRY RUN COMPLETED - NO CHANGES SAVED")
    print("======================================================================")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Migrate cashier/cashbox core tables.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    migrate(apply=not args.dry_run)


if __name__ == "__main__":
    main()
