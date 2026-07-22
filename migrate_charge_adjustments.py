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

try:
    from audit_logger import audit_log
except Exception:
    audit_log = None


# Универсальный каталог оснований для уменьшения начисления.
# BENEFIT — льгота, REWARD — поощрение, MANUAL — отдельное решение/корректировка.
# Размер не зашивается в коде категории: он задаётся при назначении конкретной квартире/авто.
SEED_ADJUSTMENTS = [
    {
        "adjustment_code": "PENSIONER",
        "adjustment_name": "Пенсионная льгота",
        "adjustment_type": "BENEFIT",
        "default_calculation_kind": "",
        "default_calculation_value": None,
        "is_sensitive": 1,
        "is_active": 1,
        "public_note_template": "Пільгові умови застосовано.",
        "internal_description": "Размер и срок определяются отдельным назначением.",
    },
    {
        "adjustment_code": "SOCIAL_OTHER",
        "adjustment_name": "Иная социальная льгота",
        "adjustment_type": "BENEFIT",
        "default_calculation_kind": "",
        "default_calculation_value": None,
        "is_sensitive": 1,
        "is_active": 1,
        "public_note_template": "Пільгові умови застосовано.",
        "internal_description": "Без раскрытия чувствительных деталей в обычной ведомости.",
    },
    {
        "adjustment_code": "OSBB_FOUNDING_REWARD",
        "adjustment_name": "Поощрение за создание ОСББ",
        "adjustment_type": "REWARD",
        "default_calculation_kind": "CREDIT",
        "default_calculation_value": None,
        "is_sensitive": 0,
        "is_active": 1,
        "public_note_template": "Індивідуальне коригування застосовано.",
        "internal_description": "Разовое или накопительное поощрение по решению ОСББ.",
    },
    {
        "adjustment_code": "GREENING_WORK_REWARD",
        "adjustment_name": "Поощрение за участие в озеленении",
        "adjustment_type": "REWARD",
        "default_calculation_kind": "CREDIT",
        "default_calculation_value": None,
        "is_sensitive": 0,
        "is_active": 1,
        "public_note_template": "Індивідуальне коригування застосовано.",
        "internal_description": "Поощрение за непосредственное участие в озеленении.",
    },
    {
        "adjustment_code": "VOLUNTEER_WORK_REWARD",
        "adjustment_name": "Поощрение за общественные работы",
        "adjustment_type": "REWARD",
        "default_calculation_kind": "CREDIT",
        "default_calculation_value": None,
        "is_sensitive": 0,
        "is_active": 1,
        "public_note_template": "Індивідуальне коригування застосовано.",
        "internal_description": "Поощрение за работы на благо ОСББ.",
    },
    {
        "adjustment_code": "MANUAL_ADJUSTMENT",
        "adjustment_name": "Индивидуальная корректировка",
        "adjustment_type": "MANUAL",
        "default_calculation_kind": "",
        "default_calculation_value": None,
        "is_sensitive": 0,
        "is_active": 1,
        "public_note_template": "Індивідуальне коригування застосовано.",
        "internal_description": "Требует явного решения/основания и аудита.",
    },
]


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def now_db():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def table_exists(cur, table_name):
    cur.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,))
    return cur.fetchone() is not None


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def add_column_if_missing(cur, table_name, column_name, column_def):
    if column_name not in table_columns(cur, table_name):
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
        return True
    return False


def ensure_adjustment_catalog(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS adjustment_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            adjustment_code TEXT NOT NULL UNIQUE,
            adjustment_name TEXT NOT NULL,
            adjustment_type TEXT NOT NULL,
            default_calculation_kind TEXT,
            default_calculation_value REAL,
            is_sensitive INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            public_note_template TEXT,
            internal_description TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        )
    """)
    added = []
    for name, definition in {
        "adjustment_name": "TEXT",
        "adjustment_type": "TEXT",
        "default_calculation_kind": "TEXT",
        "default_calculation_value": "REAL",
        "is_sensitive": "INTEGER NOT NULL DEFAULT 0",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
        "public_note_template": "TEXT",
        "internal_description": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    }.items():
        if add_column_if_missing(cur, "adjustment_catalog", name, definition):
            added.append(name)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_adjustment_catalog_type ON adjustment_catalog(adjustment_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_adjustment_catalog_active ON adjustment_catalog(is_active)")
    return added


def ensure_adjustment_assignments(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS adjustment_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            adjustment_code TEXT NOT NULL,
            adjustment_type TEXT NOT NULL,
            apartment_id INTEGER,
            apartment_number TEXT,
            contact_id INTEGER,
            vehicle_id INTEGER,
            base_service_code TEXT,
            service_item_code TEXT,
            calculation_kind TEXT NOT NULL,
            calculation_value REAL,
            credit_total_amount REAL,
            credit_remaining_amount REAL,
            valid_from TEXT,
            valid_to TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            approval_reference TEXT,
            public_note TEXT,
            internal_note TEXT,
            approved_by TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            FOREIGN KEY (adjustment_code) REFERENCES adjustment_catalog(adjustment_code)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_adjust_assign_code ON adjustment_assignments(adjustment_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_adjust_assign_apt ON adjustment_assignments(apartment_number)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_adjust_assign_vehicle ON adjustment_assignments(vehicle_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_adjust_assign_status_dates ON adjustment_assignments(status, valid_from, valid_to)")


def ensure_charge_adjustments(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS charge_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            charge_id INTEGER NOT NULL,
            adjustment_assignment_id INTEGER,
            adjustment_code TEXT NOT NULL,
            adjustment_type TEXT NOT NULL,
            calculation_kind TEXT NOT NULL,
            applied_amount REAL NOT NULL,
            public_note TEXT,
            internal_note TEXT,
            reason_snapshot TEXT,
            applied_by TEXT,
            source_context TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (charge_id) REFERENCES charges(id),
            FOREIGN KEY (adjustment_assignment_id) REFERENCES adjustment_assignments(id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_charge_adjustments_charge ON charge_adjustments(charge_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_charge_adjustments_assignment ON charge_adjustments(adjustment_assignment_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_charge_adjustments_code ON charge_adjustments(adjustment_code)")


def ensure_charge_columns(cur):
    if not table_exists(cur, "charges"):
        raise RuntimeError("Не найдена таблица charges.")
    added = []
    additions = {
        # amount остаётся итоговой суммой к оплате после корректировок.
        "gross_amount": "REAL",
        "adjustment_amount": "REAL NOT NULL DEFAULT 0",
        "net_amount": "REAL",
        "adjustment_public_note": "TEXT",
        "adjustment_internal_note": "TEXT",
        "adjustment_updated_at": "TEXT",
    }
    for name, definition in additions.items():
        if add_column_if_missing(cur, "charges", name, definition):
            added.append(name)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_charges_adjustment_amount ON charges(adjustment_amount)")
    return added


def backfill_existing_charges(cur):
    columns = table_columns(cur, "charges")
    if "amount" not in columns:
        raise RuntimeError("В таблице charges отсутствует рабочая колонка amount.")
    sets = []
    if "gross_amount" in columns:
        sets.append("gross_amount = COALESCE(gross_amount, amount)")
    if "adjustment_amount" in columns:
        sets.append("adjustment_amount = COALESCE(adjustment_amount, 0)")
    if "net_amount" in columns:
        sets.append("net_amount = COALESCE(net_amount, amount)")
    if sets:
        cur.execute(f"UPDATE charges SET {', '.join(sets)}")
    cur.execute("SELECT COUNT(*) FROM charges")
    return cur.fetchone()[0]


def upsert_catalog_row(cur, values):
    cur.execute("SELECT id FROM adjustment_catalog WHERE adjustment_code = ?", (values["adjustment_code"],))
    existing = cur.fetchone()
    if existing:
        cur.execute("""
            UPDATE adjustment_catalog
            SET adjustment_name=?, adjustment_type=?, default_calculation_kind=?,
                default_calculation_value=?, is_sensitive=?, is_active=?,
                public_note_template=?, internal_description=?, updated_at=?
            WHERE adjustment_code=?
        """, (
            values["adjustment_name"], values["adjustment_type"],
            values["default_calculation_kind"], values["default_calculation_value"],
            values["is_sensitive"], values["is_active"],
            values["public_note_template"], values["internal_description"],
            now_db(), values["adjustment_code"],
        ))
        return False
    cur.execute("""
        INSERT INTO adjustment_catalog (
            adjustment_code, adjustment_name, adjustment_type,
            default_calculation_kind, default_calculation_value,
            is_sensitive, is_active, public_note_template,
            internal_description, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        values["adjustment_code"], values["adjustment_name"], values["adjustment_type"],
        values["default_calculation_kind"], values["default_calculation_value"],
        values["is_sensitive"], values["is_active"], values["public_note_template"],
        values["internal_description"], now_db(),
    ))
    return True


def seed_catalog(cur):
    inserted = updated = 0
    for row in SEED_ADJUSTMENTS:
        if upsert_catalog_row(cur, row):
            inserted += 1
        else:
            updated += 1
    return inserted, updated


def migration_summary(cur):
    result = {}
    for table_name in ["adjustment_catalog", "adjustment_assignments", "charge_adjustments", "charges"]:
        if table_exists(cur, table_name):
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")
            result[table_name] = cur.fetchone()[0]
        else:
            result[table_name] = "NO TABLE"
    return result


def migrate(apply=True):
    conn = sqlite3.connect(get_db_file())
    cur = conn.cursor()

    print("=" * 78)
    print("CHARGE ADJUSTMENTS MIGRATION")
    print("=" * 78)
    print("DB:", get_db_file())
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", apply)
    print()

    catalog_added = ensure_adjustment_catalog(cur)
    ensure_adjustment_assignments(cur)
    ensure_charge_adjustments(cur)
    charge_added = ensure_charge_columns(cur)
    backfilled_charges = backfill_existing_charges(cur)
    catalog_inserted, catalog_updated = seed_catalog(cur)

    audit_done = False
    if audit_log is not None:
        try:
            audit_log(
                conn=conn,
                actor_type="system",
                operator_id="system",
                user_id="system",
                action_type="charge_adjustments_migration",
                table_name="charges",
                row_id="",
                field_name="",
                old_value="",
                new_value="",
                source_context="migrate_charge_adjustments.py",
                comment=(
                    "Создан механизм льгот и поощрений. Обычная ведомость продолжает "
                    "использовать только итоговую amount."
                ),
                extra={
                    "catalog_added": catalog_added,
                    "charge_added": charge_added,
                    "charges_backfilled": backfilled_charges,
                    "catalog_inserted": catalog_inserted,
                    "catalog_updated": catalog_updated,
                },
                commit=False,
            )
            audit_done = True
        except Exception as exc:
            print("WARNING: audit record not written:", exc)

    if apply:
        conn.commit()
    else:
        conn.rollback()

    if not apply:
        conn.close()
        conn = sqlite3.connect(get_db_file())
        cur = conn.cursor()

    counts = migration_summary(cur)
    conn.close()

    print("Catalog columns added:", ", ".join(catalog_added) if catalog_added else "none")
    print("Charges columns added:", ", ".join(charge_added) if charge_added else "none")
    print("Existing charges synchronized:", backfilled_charges)
    print("Adjustment catalog inserted:", catalog_inserted)
    print("Adjustment catalog updated :", catalog_updated)
    print("Audit row written:", "yes" if audit_done else "no")
    print()
    print("Counts:")
    for name, value in counts.items():
        print(f"  {name:24}: {value}")
    print()
    print(
        "Важно: ни одной льготы и ни одного поощрения эта миграция не назначает. "
        "Все существующие начисления остались без скидки: amount = net_amount, adjustment_amount = 0."
    )
    print("=" * 78)
    print("MIGRATION COMPLETED" if apply else "DRY RUN COMPLETED - NO CHANGES SAVED")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Создать универсальный механизм льгот и поощрительных корректировок "
            "без добавления лишних колонок в обычную ведомость."
        )
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    migrate(apply=not args.dry_run)


if __name__ == "__main__":
    main()
