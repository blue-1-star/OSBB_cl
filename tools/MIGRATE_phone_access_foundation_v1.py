#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
MIGRATE_phone_access_foundation_v1.py

OSBB phone access foundation migration v1.

Purpose:
  Prepare live-services sandbox DB for phone access acceptance tests:
  - create missing service_interests table if absent;
  - create missing phone_barrier_access_* tables if absent;
  - seed access points:
      BARRIER_FAR_01
      BARRIER_NEAR_02

Safety:
  - explicit --db is required;
  - DRY RUN / READ ONLY by default;
  - writes only with --apply;
  - creates DB backup before APPLY;
  - does not touch charges/payments/service_orders;
  - idempotent/re-runnable.

PowerShell:

  python .\OSBB\tools\MIGRATE_phone_access_foundation_v1.py --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db"

  python .\OSBB\tools\MIGRATE_phone_access_foundation_v1.py --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db" --apply
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


FOUNDATION_TABLES: dict[str, str] = {
    "service_interests": """
CREATE TABLE service_interests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interest_number TEXT,
    resident_account_id INTEGER,
    telegram_user_id INTEGER,
    apartment_id INTEGER,
    apartment_number TEXT,
    service_code TEXT,
    service_item_code TEXT,
    service_name_snapshot TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price_snapshot REAL NOT NULL DEFAULT 0,
    amount_due_snapshot REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'UAH',
    status TEXT NOT NULL DEFAULT 'NEW',
    source_context TEXT,
    resident_comment TEXT,
    operator_comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
)
""",
    "phone_barrier_access_interests": """
CREATE TABLE phone_barrier_access_interests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_interest_id INTEGER NOT NULL,
    requested_phone TEXT,
    parking_debt_check_mode TEXT,
    parking_debt_check_note TEXT,
    status TEXT NOT NULL DEFAULT 'NEW',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
)
""",
    "phone_barrier_access_points": """
CREATE TABLE phone_barrier_access_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    access_point_code TEXT NOT NULL UNIQUE,
    access_point_name_uk TEXT,
    access_point_name_ru TEXT,
    access_point_name_en TEXT,
    geos_access_point_id TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
)
""",
    "phone_barrier_access_interest_points": """
CREATE TABLE phone_barrier_access_interest_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_access_interest_id INTEGER NOT NULL,
    access_point_code TEXT NOT NULL,
    access_point_name_snapshot TEXT,
    status TEXT NOT NULL DEFAULT 'REQUESTED',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
)
""",
    "phone_barrier_access_order_points": """
CREATE TABLE phone_barrier_access_order_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_order_id INTEGER NOT NULL,
    access_point_code TEXT NOT NULL,
    access_point_name_snapshot TEXT,
    requested_phone TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING_ACTIVATION',
    activated_at TEXT,
    deactivated_at TEXT,
    actor_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT
)
""",
}

INDEXES: list[tuple[str, str]] = [
    ("idx_service_interests_apartment", "CREATE INDEX IF NOT EXISTS idx_service_interests_apartment ON service_interests(apartment_number)"),
    ("idx_service_interests_status", "CREATE INDEX IF NOT EXISTS idx_service_interests_status ON service_interests(status)"),
    ("idx_service_interests_service_item", "CREATE INDEX IF NOT EXISTS idx_service_interests_service_item ON service_interests(service_item_code)"),
    ("idx_phone_interest_interest", "CREATE INDEX IF NOT EXISTS idx_phone_interest_interest ON phone_barrier_access_interests(service_interest_id)"),
    ("idx_phone_interest_points_interest", "CREATE INDEX IF NOT EXISTS idx_phone_interest_points_interest ON phone_barrier_access_interest_points(phone_access_interest_id)"),
    ("idx_phone_order_points_order", "CREATE INDEX IF NOT EXISTS idx_phone_order_points_order ON phone_barrier_access_order_points(service_order_id)"),
    ("idx_phone_order_points_status", "CREATE INDEX IF NOT EXISTS idx_phone_order_points_status ON phone_barrier_access_order_points(status)"),
]

ACCESS_POINTS = [
    {
        "access_point_code": "BARRIER_FAR_01",
        "access_point_name_uk": "Дальній шлагбаум №1",
        "access_point_name_ru": "Дальний шлагбаум №1",
        "access_point_name_en": "Far barrier #1",
        "sort_order": 10,
    },
    {
        "access_point_code": "BARRIER_NEAR_02",
        "access_point_name_uk": "Ближній шлагбаум №2",
        "access_point_name_ru": "Ближний шлагбаум №2",
        "access_point_name_en": "Near barrier #2",
        "sort_order": 20,
    },
]


def connect(db_path: Path, readonly: bool) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row["name"] for row in cur.fetchall()}


def index_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,))
    return cur.fetchone() is not None


def backup_db(db_path: Path) -> Path:
    out_dir = db_path.parent / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{db_path.stem}_before_phone_access_foundation_v1_{datetime.now():%Y-%m-%d_%H-%M-%S}{db_path.suffix}"
    shutil.copy2(db_path, dst)
    return dst


def access_point_exists(cur: sqlite3.Cursor, code: str) -> bool:
    if not table_exists(cur, "phone_barrier_access_points"):
        return False
    cols = table_columns(cur, "phone_barrier_access_points")
    if "access_point_code" not in cols:
        return False
    cur.execute("SELECT 1 FROM phone_barrier_access_points WHERE access_point_code = ?", (code,))
    return cur.fetchone() is not None


def plan(cur: sqlite3.Cursor) -> dict[str, Any]:
    missing_tables = [name for name in FOUNDATION_TABLES if not table_exists(cur, name)]
    missing_indexes = [name for name, _sql in INDEXES if not index_exists(cur, name)]
    existing_points = []
    missing_points = []
    for point in ACCESS_POINTS:
        code = point["access_point_code"]
        if access_point_exists(cur, code):
            existing_points.append(code)
        else:
            missing_points.append(code)
    return {
        "missing_tables": missing_tables,
        "missing_indexes": missing_indexes,
        "existing_points": existing_points,
        "missing_points": missing_points,
    }


def apply_migration(conn: sqlite3.Connection) -> dict[str, int]:
    cur = conn.cursor()
    created_tables = 0
    created_indexes = 0
    seeded_points = 0

    for name, ddl in FOUNDATION_TABLES.items():
        if not table_exists(cur, name):
            cur.execute(ddl)
            created_tables += 1

    for _name, sql in INDEXES:
        cur.execute(sql)
        created_indexes += 1

    for point in ACCESS_POINTS:
        if not access_point_exists(cur, point["access_point_code"]):
            cur.execute(
                """
                INSERT INTO phone_barrier_access_points (
                    access_point_code,
                    access_point_name_uk,
                    access_point_name_ru,
                    access_point_name_en,
                    is_active,
                    sort_order,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    point["access_point_code"],
                    point["access_point_name_uk"],
                    point["access_point_name_ru"],
                    point["access_point_name_en"],
                    int(point["sort_order"]),
                ),
            )
            seeded_points += 1

    return {
        "created_tables": created_tables,
        "created_indexes": created_indexes,
        "seeded_points": seeded_points,
    }


def verify(cur: sqlite3.Cursor) -> list[str]:
    lines = []
    lines.append("Tables:")
    for name in FOUNDATION_TABLES:
        lines.append(f" - {name}: {'OK' if table_exists(cur, name) else 'MISSING'}")

    lines.append("")
    lines.append("Access points:")
    if table_exists(cur, "phone_barrier_access_points"):
        cols = table_columns(cur, "phone_barrier_access_points")
        if "access_point_code" in cols:
            cur.execute(
                """
                SELECT access_point_code, access_point_name_uk, is_active, sort_order
                FROM phone_barrier_access_points
                ORDER BY sort_order, access_point_code
                """
            )
            for row in cur.fetchall():
                lines.append(
                    f" - {row['access_point_code']} | active={row['is_active']} | {row['access_point_name_uk']}"
                )
        else:
            lines.append(" - access_point_code column missing")
    else:
        lines.append(" - table missing")

    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Explicit SQLite DB path.")
    ap.add_argument("--apply", action="store_true", help="Create tables/seed access points.")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    print("=" * 88)
    print("OSBB phone access foundation migration v1")
    print("=" * 88)
    print("Mode:", "APPLY" if args.apply else "DRY RUN / READ ONLY")
    print("DB:", db_path)
    print("")

    conn = connect(db_path, readonly=not args.apply)
    try:
        cur = conn.cursor()
        p = plan(cur)

        print("Missing tables:")
        if p["missing_tables"]:
            for name in p["missing_tables"]:
                print(" -", name)
        else:
            print(" - none")
        print("")

        print("Missing indexes:")
        if p["missing_indexes"]:
            for name in p["missing_indexes"]:
                print(" -", name)
        else:
            print(" - none")
        print("")

        print("Access points:")
        print(" - existing:", ", ".join(p["existing_points"]) or "-")
        print(" - missing :", ", ".join(p["missing_points"]) or "-")
        print("")

        if not args.apply:
            print("VERIFY preview:")
            for line in verify(cur):
                print(line)
            print("")
            print("DRY RUN COMPLETED. Re-run with --apply to migrate.")
            return 0

        backup = backup_db(db_path)
        stats = apply_migration(conn)
        conn.commit()

        print("Backup:", backup)
        print("Created tables:", stats["created_tables"])
        print("Created indexes:", stats["created_indexes"])
        print("Seeded access points:", stats["seeded_points"])
        print("")
        print("VERIFY after apply:")
        for line in verify(cur):
            print(line)
        print("")
        print("APPLY COMPLETED")
        return 0

    except Exception:
        if args.apply:
            conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
