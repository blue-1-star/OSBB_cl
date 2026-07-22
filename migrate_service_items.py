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
from audit_logger import audit_log


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def now_db():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def table_exists(cur, table_name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
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


def create_service_catalog(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS service_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_code TEXT NOT NULL UNIQUE,
            service_name TEXT NOT NULL,
            service_type TEXT NOT NULL,
            service_group TEXT NOT NULL DEFAULT 'GENERAL',
            category TEXT,
            is_monthly INTEGER NOT NULL DEFAULT 0,
            is_fundraising INTEGER NOT NULL DEFAULT 0,
            is_commercial INTEGER NOT NULL DEFAULT 0,
            is_access_control INTEGER NOT NULL DEFAULT 0,
            is_cash_collectable INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)
    added = []
    migrations = {
        "service_type": "TEXT",
        "service_group": "TEXT NOT NULL DEFAULT 'GENERAL'",
        "category": "TEXT",
        "is_monthly": "INTEGER NOT NULL DEFAULT 0",
        "is_fundraising": "INTEGER NOT NULL DEFAULT 0",
        "is_commercial": "INTEGER NOT NULL DEFAULT 0",
        "is_access_control": "INTEGER NOT NULL DEFAULT 0",
        "is_cash_collectable": "INTEGER NOT NULL DEFAULT 1",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
        "comment": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    }
    for col, col_def in migrations.items():
        if add_column_if_missing(cur, "service_catalog", col, col_def):
            added.append(col)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_service_catalog_code ON service_catalog(service_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_service_catalog_type ON service_catalog(service_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_service_catalog_group ON service_catalog(service_group)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_service_catalog_active ON service_catalog(is_active)")
    return added


def create_service_items(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS service_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_item_code TEXT NOT NULL UNIQUE,
            service_code TEXT NOT NULL,
            service_item_name TEXT NOT NULL,
            service_type TEXT NOT NULL,
            period_code TEXT,
            sequence_no INTEGER,
            amount_default REAL,
            currency TEXT NOT NULL DEFAULT 'UAH',
            date_from TEXT,
            date_to TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            is_active INTEGER NOT NULL DEFAULT 1,
            description TEXT,
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            FOREIGN KEY (service_code) REFERENCES service_catalog(service_code)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_service_items_code ON service_items(service_item_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_service_items_service ON service_items(service_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_service_items_period ON service_items(period_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_service_items_type ON service_items(service_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_service_items_active ON service_items(is_active)")


def create_barrier_access(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS barrier_phone_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            apartment_number TEXT,
            contact_id INTEGER,
            phone_number TEXT,
            access_status TEXT NOT NULL DEFAULT 'ACTIVE',
            status_reason TEXT,
            blocked_by TEXT,
            blocked_at TEXT,
            unblocked_by TEXT,
            unblocked_at TEXT,
            debt_amount REAL DEFAULT 0,
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_barrier_phone_access_apt ON barrier_phone_access(apartment_number)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_barrier_phone_access_phone ON barrier_phone_access(phone_number)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_barrier_phone_access_status ON barrier_phone_access(access_status)")


def ensure_financial_columns(cur):
    added = {}
    for table in ["charges", "payments", "cashbox_operations", "payment_allocations"]:
        if not table_exists(cur, table):
            continue
        added[table] = []
        for col, col_def in {
            "service_item_code": "TEXT",
            "base_service_code": "TEXT",
            "service_type": "TEXT",
        }.items():
            if add_column_if_missing(cur, table, col, col_def):
                added[table].append(col)

        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_service_item ON {table}(service_item_code)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_base_service ON {table}(base_service_code)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_service_type ON {table}(service_type)")
    return added


def service_group_for(service_type, category):
    if service_type == "MONTHLY":
        return "MONTHLY"
    if service_type == "FUNDRAISING":
        return "FUNDRAISING"
    if service_type == "COMMERCIAL":
        return "COMMERCIAL"
    return category or "GENERAL"


def get_required_columns_with_defaults(cur, table_name):
    """
    Existing DB may already have legacy NOT NULL columns.
    Fill them when inserting dynamically.
    PRAGMA table_info fields:
      cid, name, type, notnull, dflt_value, pk
    """
    cur.execute(f"PRAGMA table_info({table_name})")
    result = {}

    for cid, name, col_type, notnull, dflt_value, pk in cur.fetchall():
        if pk:
            continue
        if not notnull:
            continue
        if dflt_value is not None:
            continue

        upper_name = (name or "").upper()
        upper_type = (col_type or "").upper()

        if name == "service_group":
            result[name] = "GENERAL"
        elif name == "unit":
            result[name] = "service"
        elif name in {"service_name", "name", "title"}:
            result[name] = "Без названия"
        elif name in {"service_code", "code"}:
            result[name] = "UNKNOWN"
        elif "AMOUNT" in upper_name or "PRICE" in upper_name or "SUM" in upper_name:
            result[name] = 0
        elif "ACTIVE" in upper_name:
            result[name] = 1
        elif "DATE" in upper_name or "TIME" in upper_name or upper_name.endswith("_AT"):
            result[name] = now_db()
        elif "INT" in upper_type:
            result[name] = 0
        elif "REAL" in upper_type or "NUM" in upper_type or "DEC" in upper_type:
            result[name] = 0
        else:
            result[name] = ""

    return result


def insert_dynamic(cur, table_name, values):
    cols = table_columns(cur, table_name)
    safe_values = dict(values)

    for col, default_value in get_required_columns_with_defaults(cur, table_name).items():
        safe_values.setdefault(col, default_value)

    if table_name == "service_catalog":
        safe_values.setdefault("unit", "service")
        safe_values.setdefault("service_group", safe_values.get("service_type") or "GENERAL")

    insert_cols = [k for k in safe_values if k in cols]
    placeholders = ",".join("?" for _ in insert_cols)

    cur.execute(
        f"INSERT INTO {table_name} ({', '.join(insert_cols)}) VALUES ({placeholders})",
        tuple(safe_values[k] for k in insert_cols),
    )
    return cur.lastrowid


def update_dynamic_by_code(cur, table_name, code_column, code_value, values):
    cols = table_columns(cur, table_name)
    sets = []
    params = []

    for key, value in values.items():
        if key in cols and key != code_column:
            sets.append(f"{key} = ?")
            params.append(value)

    if not sets:
        return False

    params.append(code_value)
    cur.execute(
        f"UPDATE {table_name} SET {', '.join(sets)} WHERE {code_column} = ?",
        tuple(params),
    )
    return cur.rowcount > 0


def upsert_service(cur, service_code, service_name, service_type, category,
                   is_monthly=0, is_fundraising=0, is_commercial=0,
                   is_access_control=0, is_cash_collectable=1, comment=""):
    group = service_group_for(service_type, category)

    values = {
        "service_code": service_code,
        "service_name": service_name,
        "service_type": service_type,
        "service_group": group,
        "category": category,
        "is_monthly": int(is_monthly),
        "is_fundraising": int(is_fundraising),
        "is_commercial": int(is_commercial),
        "is_access_control": int(is_access_control),
        "is_cash_collectable": int(is_cash_collectable),
        "is_active": 1,
        "comment": comment,
        "unit": "service",
        "created_at": now_db(),
        "updated_at": now_db(),
    }

    cur.execute("SELECT id FROM service_catalog WHERE service_code = ?", (service_code,))
    row = cur.fetchone()

    if row:
        update_values = dict(values)
        update_values.pop("service_code", None)
        update_values.pop("created_at", None)
        update_dynamic_by_code(cur, "service_catalog", "service_code", service_code, update_values)
        return False

    insert_dynamic(cur, "service_catalog", values)
    return True


def seed_service_catalog(cur):
    services = [
        ("PARKING_DAY", "Парковка Day", "MONTHLY", "PARKING", 1, 0, 0, 0, 1, "Ежемесячная парковка, дневной тариф"),
        ("PARKING_NIGHT", "Парковка Night", "MONTHLY", "PARKING", 1, 0, 0, 0, 1, "Ежемесячная парковка, ночной/суточный тариф"),
        ("BARRIER_PHONE", "Телефонное открытие шлагбаума", "MONTHLY", "ACCESS", 1, 0, 0, 1, 0, "Управляемый доступ; блокируется при задолженности"),
        ("IMPROVEMENT", "Благоустройство", "FUNDRAISING", "IMPROVEMENT", 0, 1, 0, 0, 1, "Разметка, асфальтирование, бытовые условия охранников"),
        ("PARKING_EQUIPMENT", "Оборудование парковки", "FUNDRAISING", "EQUIPMENT", 0, 1, 0, 0, 1, "Камеры, регистратор, распознавание номеров, сеть"),
        ("BARRIER_REPAIR", "Ремонт / сбор на шлагбаум", "FUNDRAISING", "BARRIER", 0, 1, 0, 0, 1, "Ремонт и обслуживание шлагбаума"),
        ("GUEST_PARKING", "Гостевая парковка", "COMMERCIAL", "PARKING", 0, 0, 1, 0, 1, "Гостевая парковка"),
        ("PARK_PLACE_RENT", "Аренда паркоместа", "COMMERCIAL", "PARKING_ASSET", 0, 0, 1, 0, 1, "Коммерческая аренда паркоместа"),
        ("PARK_PLACE_SALE", "Продажа паркоместа", "COMMERCIAL", "PARKING_ASSET", 0, 0, 1, 0, 1, "Коммерческая продажа паркоместа"),
    ]
    ins = upd = 0
    for row in services:
        if upsert_service(cur, *row):
            ins += 1
        else:
            upd += 1
    return ins, upd


def upsert_service_item(cur, service_item_code, service_code, service_item_name,
                        service_type, period_code=None, sequence_no=None,
                        amount_default=None, date_from=None, date_to=None,
                        description="", comment=""):
    cur.execute("SELECT id FROM service_items WHERE service_item_code = ?", (service_item_code,))
    row = cur.fetchone()
    if row:
        cur.execute("""
            UPDATE service_items
            SET service_code=?, service_item_name=?, service_type=?, period_code=?,
                sequence_no=?, amount_default=?, date_from=?, date_to=?,
                description=?, comment=?, updated_at=?
            WHERE service_item_code=?
        """, (
            service_code, service_item_name, service_type, period_code,
            sequence_no, amount_default, date_from, date_to,
            description, comment, now_db(), service_item_code
        ))
        return False

    cur.execute("""
        INSERT INTO service_items (
            service_item_code, service_code, service_item_name, service_type,
            period_code, sequence_no, amount_default, currency,
            date_from, date_to, status, is_active, description, comment, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'UAH', ?, ?, 'active', 1, ?, ?, ?)
    """, (
        service_item_code, service_code, service_item_name, service_type,
        period_code, sequence_no, amount_default, date_from, date_to,
        description, comment, now_db()
    ))
    return True


def seed_initial_service_items(cur):
    items = [
        ("01_Imp", "IMPROVEMENT", "Благоустройство — сбор 01", "FUNDRAISING",
         None, 1, None, None, None, "Первый сбор на благоустройство", "Описание уточняется"),
        ("01_ParkEquip", "PARKING_EQUIPMENT", "Оборудование парковки — сбор 01", "FUNDRAISING",
         None, 1, None, None, None, "Видео/регистратор/распознавание номеров", "Будущий сбор"),
        ("01_BarrierRepair", "BARRIER_REPAIR", "Ремонт шлагбаума — сбор 01", "FUNDRAISING",
         None, 1, 1000, None, None, "Сбор на ремонт шлагбаума", "В Охорона.xlsx обозначался Ш"),
    ]
    ins = upd = 0
    for item in items:
        if upsert_service_item(cur, *item):
            ins += 1
        else:
            upd += 1
    return ins, upd


def print_counts(cur):
    for table in ["service_catalog", "service_items", "barrier_phone_access", "charges", "payments", "cashbox_operations"]:
        if table_exists(cur, table):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            print(f"{table:24}: {cur.fetchone()[0]}")
        else:
            print(f"{table:24}: NO TABLE")


def migrate(apply=True):
    db_file = get_db_file()
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    print("=" * 70)
    print("SERVICE ITEMS MIGRATION")
    print("=" * 70)
    print("DB:", db_file)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", apply)
    print("")

    catalog_added = create_service_catalog(cur)
    create_service_items(cur)
    create_barrier_access(cur)
    financial_added = ensure_financial_columns(cur)
    service_ins, service_upd = seed_service_catalog(cur)
    item_ins, item_upd = seed_initial_service_items(cur)

    audit_log(
        conn=conn,
        actor_type="system",
        operator_id="system",
        user_id="system",
        action_type="service_items_migration",
        table_name="service_catalog",
        row_id="",
        field_name="",
        old_value="",
        new_value="",
        source_context="migrate_service_items.py",
        comment="Создана архитектура base service / service item",
        extra={
            "service_inserted": service_ins,
            "service_updated": service_upd,
            "item_inserted": item_ins,
            "item_updated": item_upd,
            "financial_added": financial_added,
        },
        commit=False,
    )

    if apply:
        conn.commit()
    else:
        conn.rollback()

    if not apply:
        conn.close()
        conn = sqlite3.connect(db_file)
        cur = conn.cursor()

    print("Catalog columns added:", ", ".join(catalog_added) if catalog_added else "none")
    print("Financial columns added:")
    if financial_added:
        for table, cols in financial_added.items():
            print(f"  {table}: {', '.join(cols) if cols else 'none'}")
    else:
        print("  none")
    print("")
    print("Services inserted:", service_ins)
    print("Services updated :", service_upd)
    print("Items inserted   :", item_ins)
    print("Items updated    :", item_upd)
    print("")
    print("Counts:")
    print_counts(cur)
    conn.close()

    print("=" * 70)
    print("MIGRATION COMPLETED" if apply else "DRY RUN COMPLETED - NO CHANGES SAVED")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Migrate service catalog and service items.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    migrate(apply=not args.dry_run)


if __name__ == "__main__":
    main()
