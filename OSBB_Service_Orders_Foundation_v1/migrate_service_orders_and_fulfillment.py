"""
Миграция общего контура сервисов, заказов, оплат и выдачи пультов.

Главный принцип:
  деньги <-> сервис/товар/цифровой доступ

Пульт — не простая отметка «принят/выдан», а заказ услуги с обязательными
шагами: приём собственного пульта или резерв товара, подтверждение оплаты,
техническое выполнение и физическая выдача.

Телефонный доступ к шлагбауму — обычная услуга в каталоге с профилем
PHONE_ACCESS_CONNECT. Особенность только в исполнении: вместо склада пультов
создаётся/активируется цифровая учётная запись доступа.

По умолчанию dry-run. Миграция:
- не удаляет исторические сервисы, заявки, платежи или движения;
- не назначает роли реальным пользователям;
- не меняет существующие цены;
- добавляет только новые таблицы, индексы и поля связей.

Запуск проверки:
g:\Programming\Py\venv\Scripts\python.exe G:\Programming\Py\OSBB\migrate_service_orders_and_fulfillment.py

Применение после sandbox-проверки:
g:\Programming\Py\venv\Scripts\python.exe G:\Programming\Py\OSBB\migrate_service_orders_and_fulfillment.py --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
for folder in (ROOT, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB

try:
    from audit_logger import audit_log
except Exception:
    audit_log = None


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, name: str) -> set[str]:
    if not table_exists(cur, name):
        return set()
    cur.execute(f'PRAGMA table_info("{name}")')
    return {row[1] for row in cur.fetchall()}


def add_column_if_missing(
    cur: sqlite3.Cursor,
    table: str,
    column: str,
    definition: str,
) -> bool:
    if not table_exists(cur, table) or column in table_columns(cur, table):
        return False
    cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')
    return True


def index_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def create_index_if_missing(cur: sqlite3.Cursor, name: str, sql: str) -> bool:
    if index_exists(cur, name):
        return False
    cur.execute(sql)
    return True


def create_tables(cur: sqlite3.Cursor) -> dict[str, bool]:
    existed: dict[str, bool] = {}

    definitions = {
        "service_workflow_profiles": """
            CREATE TABLE IF NOT EXISTS service_workflow_profiles (
                profile_code TEXT PRIMARY KEY,
                profile_name TEXT NOT NULL,
                service_category TEXT NOT NULL,
                description TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
        """,
        "service_workflow_steps": """
            CREATE TABLE IF NOT EXISTS service_workflow_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_code TEXT NOT NULL,
                step_code TEXT NOT NULL,
                step_name TEXT NOT NULL,
                step_kind TEXT NOT NULL,
                sequence_no INTEGER NOT NULL DEFAULT 1,
                is_required INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                description TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                UNIQUE(profile_code, step_code)
            )
        """,
        "service_item_workflows": """
            CREATE TABLE IF NOT EXISTS service_item_workflows (
                service_item_code TEXT PRIMARY KEY,
                workflow_profile_code TEXT NOT NULL,
                resident_request_enabled INTEGER NOT NULL DEFAULT 0,
                operator_create_enabled INTEGER NOT NULL DEFAULT 1,
                requires_charge INTEGER NOT NULL DEFAULT 1,
                payment_timing TEXT NOT NULL DEFAULT 'BEFORE_FULFILLMENT',
                inventory_mode TEXT NOT NULL DEFAULT 'NONE',
                resident_asset_mode TEXT NOT NULL DEFAULT 'NONE',
                is_active INTEGER NOT NULL DEFAULT 1,
                retired_at TEXT,
                retired_reason TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
        """,
        "service_price_versions": """
            CREATE TABLE IF NOT EXISTS service_price_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_item_code TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'UAH',
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_by TEXT,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                UNIQUE(service_item_code, effective_from)
            )
        """,
        "service_orders": """
            CREATE TABLE IF NOT EXISTS service_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT NOT NULL UNIQUE,
                resident_account_id INTEGER,
                telegram_user_id TEXT,
                apartment_id INTEGER,
                apartment_number TEXT NOT NULL,
                service_code TEXT,
                service_item_code TEXT NOT NULL,
                service_name_snapshot TEXT NOT NULL,
                workflow_profile_code TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 1,
                unit_price_snapshot REAL,
                amount_due_snapshot REAL,
                currency TEXT NOT NULL DEFAULT 'UAH',
                order_status TEXT NOT NULL DEFAULT 'REQUESTED',
                payment_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED',
                fulfillment_status TEXT NOT NULL DEFAULT 'NOT_STARTED',
                resident_comment TEXT,
                operator_comment TEXT,
                requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                completed_at TEXT,
                cancelled_at TEXT,
                cancelled_reason TEXT
            )
        """,
        "service_order_steps": """
            CREATE TABLE IF NOT EXISTS service_order_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_order_id INTEGER NOT NULL,
                step_code TEXT NOT NULL,
                step_name TEXT NOT NULL,
                step_kind TEXT NOT NULL,
                sequence_no INTEGER NOT NULL DEFAULT 1,
                is_required INTEGER NOT NULL DEFAULT 1,
                step_status TEXT NOT NULL DEFAULT 'WAITING',
                confirmed_by TEXT,
                confirmed_at TEXT,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                UNIQUE(service_order_id, step_code)
            )
        """,
        "service_order_charge_links": """
            CREATE TABLE IF NOT EXISTS service_order_charge_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_order_id INTEGER NOT NULL,
                charge_id INTEGER NOT NULL,
                linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                linked_by TEXT,
                note TEXT,
                UNIQUE(service_order_id, charge_id)
            )
        """,
        "service_order_payment_links": """
            CREATE TABLE IF NOT EXISTS service_order_payment_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_order_id INTEGER NOT NULL,
                payment_id INTEGER NOT NULL,
                amount REAL,
                linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                linked_by TEXT,
                note TEXT,
                UNIQUE(service_order_id, payment_id)
            )
        """,
        "service_order_events": """
            CREATE TABLE IF NOT EXISTS service_order_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_order_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor_id TEXT,
                actor_role TEXT,
                source_context TEXT,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "remote_assets": """
            CREATE TABLE IF NOT EXISTS remote_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_number TEXT NOT NULL UNIQUE,
                asset_type TEXT NOT NULL DEFAULT 'REMOTE',
                ownership_type TEXT NOT NULL,
                inventory_status TEXT NOT NULL DEFAULT 'AVAILABLE',
                condition_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                hardware_model TEXT,
                serial_number TEXT,
                programming_identifier TEXT,
                apartment_id INTEGER,
                apartment_number TEXT,
                received_at TEXT,
                issued_at TEXT,
                retired_at TEXT,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
        """,
        "remote_asset_movements": """
            CREATE TABLE IF NOT EXISTS remote_asset_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                remote_asset_id INTEGER NOT NULL,
                service_order_id INTEGER,
                movement_type TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT,
                apartment_id INTEGER,
                apartment_number TEXT,
                post_code TEXT,
                actor_id TEXT,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "remote_order_details": """
            CREATE TABLE IF NOT EXISTS remote_order_details (
                service_order_id INTEGER PRIMARY KEY,
                resident_asset_id INTEGER,
                issued_asset_id INTEGER,
                remote_owner_mode TEXT NOT NULL DEFAULT 'NONE',
                reprogramming_required INTEGER NOT NULL DEFAULT 0,
                inventory_asset_required INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
        """,
        "service_access_credentials": """
            CREATE TABLE IF NOT EXISTS service_access_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_order_id INTEGER,
                credential_kind TEXT NOT NULL,
                credential_value TEXT NOT NULL,
                access_scope TEXT NOT NULL DEFAULT 'BARRIER',
                credential_status TEXT NOT NULL DEFAULT 'REQUESTED',
                external_reference TEXT,
                apartment_id INTEGER,
                apartment_number TEXT,
                activated_by TEXT,
                activated_at TEXT,
                disabled_at TEXT,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
        """,
    }

    for name, sql in definitions.items():
        existed[name] = table_exists(cur, name)
        cur.execute(sql)

    return {name: not value for name, value in existed.items()}


def seed_workflow_profiles(cur: sqlite3.Cursor) -> dict[str, int]:
    profiles = [
        (
            "GENERAL_PAID",
            "Общая платная услуга",
            "GENERAL",
            "Оплата и выполнение обычной услуги.",
        ),
        (
            "PHONE_ACCESS_CONNECT",
            "Подключение телефонного доступа",
            "ACCESS",
            "Разовая услуга: оплата и активация цифрового доступа.",
        ),
        (
            "REMOTE_REPROGRAM_OWN",
            "Перепрошивка пульта жильца",
            "REMOTE",
            "Житель сдаёт свой пульт; после оплаты и перепрошивки получает тот же пульт.",
        ),
        (
            "REMOTE_NEW_FROM_STOCK",
            "Новый пульт со склада",
            "REMOTE",
            "Резерв товара, подтверждение оплаты, выдача конкретного пульта.",
        ),
        (
            "REMOTE_REFURBISHED_FROM_STOCK",
            "Восстановленный пульт со склада",
            "REMOTE",
            "Резерв восстановленного пульта, подтверждение оплаты, выдача.",
        ),
    ]

    steps = {
        "GENERAL_PAID": [
            ("PAYMENT_CONFIRMED", "Оплата подтверждена", "PAYMENT", 10),
            ("SERVICE_DELIVERED", "Услуга выполнена", "FULFILLMENT", 20),
        ],
        "PHONE_ACCESS_CONNECT": [
            ("PAYMENT_CONFIRMED", "Оплата подтверждена", "PAYMENT", 10),
            ("DIGITAL_ACCESS_ACTIVATED", "Телефонный доступ активирован", "DIGITAL_ACCESS", 20),
        ],
        "REMOTE_REPROGRAM_OWN": [
            ("RESIDENT_REMOTE_RECEIVED", "Пульт жильца принят", "RESIDENT_ASSET_IN", 10),
            ("PAYMENT_CONFIRMED", "Оплата подтверждена", "PAYMENT", 20),
            ("REMOTE_PROGRAMMED", "Пульт перепрошит", "TECHNICAL_FULFILLMENT", 30),
            ("RESIDENT_REMOTE_RETURNED", "Пульт возвращён жильцу", "RESIDENT_ASSET_OUT", 40),
        ],
        "REMOTE_NEW_FROM_STOCK": [
            ("STOCK_ASSET_RESERVED", "Пульт зарезервирован со склада", "INVENTORY_RESERVATION", 10),
            ("PAYMENT_CONFIRMED", "Оплата подтверждена", "PAYMENT", 20),
            ("STOCK_ASSET_ISSUED", "Пульт выдан жильцу", "INVENTORY_ISSUE", 30),
        ],
        "REMOTE_REFURBISHED_FROM_STOCK": [
            ("STOCK_ASSET_RESERVED", "Восстановленный пульт зарезервирован", "INVENTORY_RESERVATION", 10),
            ("PAYMENT_CONFIRMED", "Оплата подтверждена", "PAYMENT", 20),
            ("STOCK_ASSET_ISSUED", "Восстановленный пульт выдан жильцу", "INVENTORY_ISSUE", 30),
        ],
    }

    profile_count = 0
    step_count = 0
    for code, name, category, description in profiles:
        cur.execute(
            """
            INSERT INTO service_workflow_profiles (
                profile_code, profile_name, service_category,
                description, is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(profile_code) DO UPDATE SET
                profile_name = excluded.profile_name,
                service_category = excluded.service_category,
                description = excluded.description,
                is_active = 1,
                updated_at = excluded.updated_at
            """,
            (code, name, category, description, now_db(), now_db()),
        )
        profile_count += 1

        for step_code, step_name, step_kind, sequence_no in steps[code]:
            cur.execute(
                """
                INSERT INTO service_workflow_steps (
                    profile_code, step_code, step_name, step_kind,
                    sequence_no, is_required, is_active,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?)
                ON CONFLICT(profile_code, step_code) DO UPDATE SET
                    step_name = excluded.step_name,
                    step_kind = excluded.step_kind,
                    sequence_no = excluded.sequence_no,
                    is_required = 1,
                    is_active = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    code, step_code, step_name, step_kind,
                    sequence_no, now_db(), now_db(),
                ),
            )
            step_count += 1

    return {"profiles_upserted": profile_count, "steps_upserted": step_count}


def seed_phone_access_mapping(cur: sqlite3.Cursor) -> bool:
    """
    Existing BARRIER_PHONE_CONNECT remains the authoritative catalog service.
    The new layer only tells the generic order engine how it is fulfilled.
    """
    if not table_exists(cur, "service_items"):
        return False
    cols = table_columns(cur, "service_items")
    if "service_item_code" not in cols:
        return False

    cur.execute(
        """
        SELECT service_item_code
        FROM service_items
        WHERE service_code = 'BARRIER_PHONE_CONNECT'
        ORDER BY id
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        return False

    item_code = str(row[0])
    cur.execute(
        """
        INSERT INTO service_item_workflows (
            service_item_code, workflow_profile_code,
            resident_request_enabled, operator_create_enabled,
            requires_charge, payment_timing,
            inventory_mode, resident_asset_mode, is_active,
            created_at, updated_at
        )
        VALUES (?, 'PHONE_ACCESS_CONNECT', 1, 1, 1,
                'BEFORE_FULFILLMENT', 'NONE', 'NONE', 1, ?, ?)
        ON CONFLICT(service_item_code) DO NOTHING
        """,
        (item_code, now_db(), now_db()),
    )
    return True


def seed_access_roles_if_available(cur: sqlite3.Cursor) -> dict[str, int]:
    if not (
        table_exists(cur, "access_roles")
        and table_exists(cur, "access_role_permissions")
    ):
        return {"roles": 0, "permissions": 0}

    role_rows = [
        (
            "SERVICE_CATALOG_MANAGER",
            "Менеджер каталога услуг",
            "Добавляет, меняет цену, активирует и архивирует актуальные услуги. Не удаляет историю.",
        ),
        (
            "REMOTE_SERVICE_OPERATOR",
            "Оператор услуг пультов",
            "Ведёт заявки, приём/выдачу и складские движения пультов.",
        ),
        (
            "ACCESS_SERVICE_OPERATOR",
            "Оператор телефонного доступа",
            "Подтверждает подключение/отключение цифрового доступа по услугам.",
        ),
    ]
    permission_rows = [
        ("SERVICE_CATALOG_MANAGER", "service_catalog", "MANAGE", "ALL", "*"),
        ("SERVICE_CATALOG_MANAGER", "service_item_workflows", "MANAGE", "ALL", "*"),
        ("SERVICE_CATALOG_MANAGER", "service_price_versions", "MANAGE", "ALL", "*"),
        ("REMOTE_SERVICE_OPERATOR", "service_orders", "VIEW", "SERVICE_CATEGORY", "REMOTE"),
        ("REMOTE_SERVICE_OPERATOR", "service_orders", "CREATE", "SERVICE_CATEGORY", "REMOTE"),
        ("REMOTE_SERVICE_OPERATOR", "service_order_steps", "CONFIRM", "SERVICE_CATEGORY", "REMOTE"),
        ("REMOTE_SERVICE_OPERATOR", "remote_assets", "VIEW", "POST", "O"),
        ("REMOTE_SERVICE_OPERATOR", "remote_assets", "MOVE", "POST", "O"),
        ("ACCESS_SERVICE_OPERATOR", "service_orders", "VIEW", "SERVICE_CATEGORY", "ACCESS"),
        ("ACCESS_SERVICE_OPERATOR", "service_order_steps", "CONFIRM", "SERVICE_CATEGORY", "ACCESS"),
        ("ACCESS_SERVICE_OPERATOR", "service_access_credentials", "ACTIVATE", "SERVICE_CATEGORY", "ACCESS"),
    ]

    roles = 0
    permissions = 0
    for role_code, role_name, description in role_rows:
        cur.execute(
            """
            INSERT INTO access_roles (
                role_code, role_name, description, is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(role_code) DO UPDATE SET
                role_name = excluded.role_name,
                description = excluded.description,
                is_active = 1,
                updated_at = excluded.updated_at
            """,
            (role_code, role_name, description, now_db(), now_db()),
        )
        roles += 1

    for role_code, resource, action, scope_type, scope_value in permission_rows:
        cur.execute(
            """
            SELECT id
            FROM access_role_permissions
            WHERE role_code = ?
              AND resource = ?
              AND action = ?
              AND scope_type = ?
              AND scope_value = ?
            """,
            (role_code, resource, action, scope_type, scope_value),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                """
                UPDATE access_role_permissions
                SET effect = 'ALLOW', is_active = 1, updated_at = ?
                WHERE id = ?
                """,
                (now_db(), int(existing[0])),
            )
        else:
            cur.execute(
                """
                INSERT INTO access_role_permissions (
                    role_code, resource, action,
                    scope_type, scope_value, effect,
                    is_active, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'ALLOW', 1, ?, ?, ?)
                """,
                (
                    role_code, resource, action,
                    scope_type, scope_value,
                    "Автоматический профиль общего контура услуг.",
                    now_db(), now_db(),
                ),
            )
            permissions += 1

    return {"roles": roles, "permissions": permissions}


def create_indexes(cur: sqlite3.Cursor) -> list[str]:
    definitions = [
        (
            "idx_service_item_workflows_profile",
            "CREATE INDEX idx_service_item_workflows_profile "
            "ON service_item_workflows(workflow_profile_code, is_active)",
        ),
        (
            "idx_service_price_versions_item",
            "CREATE INDEX idx_service_price_versions_item "
            "ON service_price_versions(service_item_code, effective_from, effective_to, is_active)",
        ),
        (
            "idx_service_orders_unit",
            "CREATE INDEX idx_service_orders_unit "
            "ON service_orders(apartment_id, order_status, requested_at)",
        ),
        (
            "idx_service_orders_item",
            "CREATE INDEX idx_service_orders_item "
            "ON service_orders(service_item_code, order_status, requested_at)",
        ),
        (
            "idx_service_order_steps_order",
            "CREATE INDEX idx_service_order_steps_order "
            "ON service_order_steps(service_order_id, sequence_no, step_status)",
        ),
        (
            "idx_service_order_events_order",
            "CREATE INDEX idx_service_order_events_order "
            "ON service_order_events(service_order_id, created_at)",
        ),
        (
            "idx_remote_assets_status",
            "CREATE INDEX idx_remote_assets_status "
            "ON remote_assets(inventory_status, condition_status)",
        ),
        (
            "idx_remote_asset_movements_asset",
            "CREATE INDEX idx_remote_asset_movements_asset "
            "ON remote_asset_movements(remote_asset_id, created_at)",
        ),
        (
            "idx_service_access_credentials_value",
            "CREATE INDEX idx_service_access_credentials_value "
            "ON service_access_credentials(credential_kind, credential_value, credential_status)",
        ),
    ]
    created = []
    for name, sql in definitions:
        if create_index_if_missing(cur, name, sql):
            created.append(name)
    return created


def extend_legacy_tables(cur: sqlite3.Cursor) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}

    legacy_fields = {
        "remote_requests": {
            "service_order_id": "INTEGER",
        },
        "remote_handover_events": {
            "service_order_id": "INTEGER",
            "remote_asset_id": "INTEGER",
            "remote_asset_movement_id": "INTEGER",
        },
        "barrier_phone_access": {
            "service_order_id": "INTEGER",
        },
    }

    for table, fields in legacy_fields.items():
        result[table] = []
        for field, definition in fields.items():
            if add_column_if_missing(cur, table, field, definition):
                result[table].append(field)

    return result


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    cur = conn.cursor()
    created = create_tables(cur)
    seeded = seed_workflow_profiles(cur)
    phone_mapping = seed_phone_access_mapping(cur)
    role_seed = seed_access_roles_if_available(cur)
    indexes = create_indexes(cur)
    legacy = extend_legacy_tables(cur)
    return {
        "tables_created": created,
        "workflow_seed": seeded,
        "phone_access_mapping_seeded": phone_mapping,
        "access_roles_seed": role_seed,
        "indexes_created": indexes,
        "legacy_columns_added": legacy,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = get_db_file()
    if not db.exists():
        raise SystemExit(f"Не найдена БД: {db}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        result = ensure_schema(conn)

        report_dir = paths.OSBB_EXPORTS_DIR / "services"
        report_dir.mkdir(parents=True, exist_ok=True)
        report = report_dir / (
            f"service_orders_fulfillment_migration_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
        )

        lines = [
            "=" * 100,
            "SERVICE ORDERS + FULFILLMENT MIGRATION",
            "=" * 100,
            f"DB: {db}",
            f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
            f"Apply: {args.apply}",
            "",
            "Created tables:",
            *[
                f"  {name}: {'created' if was_created else 'already existed'}"
                for name, was_created in result["tables_created"].items()
            ],
            "",
            f"Workflow seed: {result['workflow_seed']}",
            f"Existing phone-access item mapped: {result['phone_access_mapping_seeded']}",
            f"Access roles seed: {result['access_roles_seed']}",
            (
                "Indexes: "
                + (", ".join(result["indexes_created"]) or "none")
            ),
            "",
            "Legacy links added:",
            *[
                f"  {table}: {', '.join(items) if items else 'none'}"
                for table, items in result["legacy_columns_added"].items()
            ],
            "",
            "Rules:",
            "  • Historical services are never deleted by this migration.",
            "  • A service item can later be archived, not erased.",
            "  • Prices are versioned in service_price_versions.",
            "  • Payment and physical/digital fulfillment are separate auditable steps.",
            "  • Roles are created without any assignment to real Telegram users.",
        ]

        if args.apply:
            if audit_log:
                audit_log(
                    conn=conn,
                    operator_id="system",
                    user_id="system",
                    actor_type="system",
                    action_type="service_orders_fulfillment_migration",
                    table_name="service_orders",
                    row_id="",
                    field_name="schema",
                    old_value="",
                    new_value="generic services/orders/remote-assets/access-credentials",
                    source_context="migrate_service_orders_and_fulfillment.py",
                    comment=(
                        "Добавлен общий контур услуг: деньги, выполнение, "
                        "пульты и телефонный доступ."
                    ),
                    extra=result,
                    commit=False,
                )
            conn.commit()
            lines.extend(["", "APPLIED"])
        else:
            conn.rollback()
            lines.extend(["", "DRY RUN COMPLETED - NO CHANGES SAVED"])

        report.write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        print("Report:", report)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
