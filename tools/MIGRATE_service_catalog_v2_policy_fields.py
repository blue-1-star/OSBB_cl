#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
MIGRATE_service_catalog_v2_policy_fields.py

OSBB service_catalog v2 policy fields migration.

Назначение:
  Добавляет в service_catalog универсальные поля политики доступа к сервисам.

Поля v2:
  access_policy_enabled   INTEGER DEFAULT 0
  access_policy_scope     TEXT DEFAULT 'NONE'
  access_policy_mode      TEXT DEFAULT 'NONE'   -- NONE / WARN / BLOCK
  access_policy_message   TEXT
  manual_review_required  INTEGER DEFAULT 0
  policy_updated_at       TEXT
  policy_updated_by       TEXT

Безопасность:
  - требует явный --db;
  - по умолчанию DRY RUN / READ ONLY;
  - запись только с --apply;
  - перед --apply делает backup .db;
  - меняет только service_catalog;
  - не трогает charges/payments/remote_requests;
  - повторный запуск безопасен.

PowerShell:

  python .\OSBB\tools\MIGRATE_service_catalog_v2_policy_fields.py --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db"

  python .\OSBB\tools\MIGRATE_service_catalog_v2_policy_fields.py --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db" --apply
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


POLICY_COLUMNS = [
    ("access_policy_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("access_policy_scope", "TEXT NOT NULL DEFAULT 'NONE'"),
    ("access_policy_mode", "TEXT NOT NULL DEFAULT 'NONE'"),
    ("access_policy_message", "TEXT"),
    ("manual_review_required", "INTEGER NOT NULL DEFAULT 0"),
    ("policy_updated_at", "TEXT"),
    ("policy_updated_by", "TEXT"),
]


# service_code -> policy values
SEED_POLICY = {
    # Hard blocks: new/issued access services.
    "TEST_REMOTE_NEW": {
        "access_policy_enabled": 1,
        "access_policy_scope": "PARKING",
        "access_policy_mode": "BLOCK",
        "manual_review_required": 0,
        "access_policy_message": (
            "По квартире есть задолженность за парковку. "
            "Заказ или выдача нового пульта временно недоступны до оплаты или сверки."
        ),
    },
    "TEST_REMOTE_REFURBISHED": {
        "access_policy_enabled": 1,
        "access_policy_scope": "PARKING",
        "access_policy_mode": "BLOCK",
        "manual_review_required": 0,
        "access_policy_message": (
            "По квартире есть задолженность за парковку. "
            "Выдача восстановленного пульта временно недоступна до оплаты или сверки."
        ),
    },
    "TEST_PHONE_ACCESS_CONNECT": {
        "access_policy_enabled": 1,
        "access_policy_scope": "PARKING",
        "access_policy_mode": "BLOCK",
        "manual_review_required": 0,
        "access_policy_message": (
            "По квартире есть задолженность за парковку. "
            "Подключение телефонного доступа временно недоступно до оплаты или сверки."
        ),
    },
    "BARRIER_PHONE_CONNECT": {
        "access_policy_enabled": 1,
        "access_policy_scope": "PARKING",
        "access_policy_mode": "BLOCK",
        "manual_review_required": 0,
        "access_policy_message": (
            "По квартире есть задолженность за парковку. "
            "Подключение или повторное подключение телефонного доступа временно недоступно."
        ),
    },
    "BARRIER_PHONE": {
        "access_policy_enabled": 1,
        "access_policy_scope": "PARKING",
        "access_policy_mode": "BLOCK",
        "manual_review_required": 0,
        "access_policy_message": (
            "По квартире есть задолженность за парковку. "
            "Телефонный доступ к шлагбауму может быть ограничен до оплаты или сверки."
        ),
    },

    # Soft warnings.
    "TEST_REMOTE_REPROGRAM_OWN": {
        "access_policy_enabled": 1,
        "access_policy_scope": "PARKING",
        "access_policy_mode": "WARN",
        "manual_review_required": 1,
        "access_policy_message": (
            "По квартире есть задолженность за парковку. "
            "Перепрошивка собственного пульта требует ручного решения оператора."
        ),
    },

    # Explicitly not blocking in v1.
    "PARKING_DAY": {
        "access_policy_enabled": 0,
        "access_policy_scope": "NONE",
        "access_policy_mode": "NONE",
        "manual_review_required": 0,
        "access_policy_message": None,
    },
    "PARKING_NIGHT": {
        "access_policy_enabled": 0,
        "access_policy_scope": "NONE",
        "access_policy_mode": "NONE",
        "manual_review_required": 0,
        "access_policy_message": None,
    },
    "IMPROVEMENT": {
        "access_policy_enabled": 0,
        "access_policy_scope": "NONE",
        "access_policy_mode": "NONE",
        "manual_review_required": 0,
        "access_policy_message": None,
    },
    "BARRIER_REPAIR": {
        "access_policy_enabled": 0,
        "access_policy_scope": "NONE",
        "access_policy_mode": "NONE",
        "manual_review_required": 0,
        "access_policy_message": None,
    },
    "PARKING_EQUIPMENT": {
        "access_policy_enabled": 0,
        "access_policy_scope": "NONE",
        "access_policy_mode": "NONE",
        "manual_review_required": 0,
        "access_policy_message": None,
    },
    "HISTORICAL_UNCLASSIFIED": {
        "access_policy_enabled": 0,
        "access_policy_scope": "NONE",
        "access_policy_mode": "NONE",
        "manual_review_required": 0,
        "access_policy_message": None,
    },
}


@dataclass
class PlannedUpdate:
    service_code: str
    exists: bool
    old_enabled: str
    old_scope: str
    old_mode: str
    old_manual_review: str
    new_enabled: str
    new_scope: str
    new_mode: str
    new_manual_review: str
    action: str


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def table_columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [row["name"] for row in cur.fetchall()]


def backup_db(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"{db_path.stem}_before_service_catalog_v2_policy_{datetime.now():%Y-%m-%d_%H-%M-%S}{db_path.suffix}"
    shutil.copy2(db_path, dst)
    return dst


def missing_policy_columns(cur: sqlite3.Cursor) -> list[tuple[str, str]]:
    existing = set(table_columns(cur, "service_catalog"))
    return [(name, ddl) for name, ddl in POLICY_COLUMNS if name not in existing]


def service_catalog_rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    cur.execute("SELECT * FROM service_catalog ORDER BY service_code")
    return [dict(row) for row in cur.fetchall()]


def get_service(cur: sqlite3.Cursor, service_code: str) -> dict[str, Any] | None:
    cur.execute("SELECT * FROM service_catalog WHERE service_code = ?", (service_code,))
    row = cur.fetchone()
    return dict(row) if row else None


def value_str(v: Any) -> str:
    return "" if v is None else str(v)


def policy_diff(row: dict[str, Any] | None, service_code: str, cols_present: bool) -> PlannedUpdate:
    policy = SEED_POLICY[service_code]
    exists = row is not None

    def old(col: str) -> str:
        if not row or not cols_present or col not in row:
            return "-"
        return value_str(row.get(col))

    old_enabled = old("access_policy_enabled")
    old_scope = old("access_policy_scope")
    old_mode = old("access_policy_mode")
    old_review = old("manual_review_required")

    new_enabled = value_str(policy["access_policy_enabled"])
    new_scope = value_str(policy["access_policy_scope"])
    new_mode = value_str(policy["access_policy_mode"])
    new_review = value_str(policy["manual_review_required"])

    if not exists:
        action = "SKIP missing service_code"
    elif not cols_present:
        action = "UPDATE after adding columns"
    elif (
        old_enabled == new_enabled
        and old_scope == new_scope
        and old_mode == new_mode
        and old_review == new_review
    ):
        action = "already matches"
    else:
        action = "UPDATE policy"

    return PlannedUpdate(
        service_code=service_code,
        exists=exists,
        old_enabled=old_enabled,
        old_scope=old_scope,
        old_mode=old_mode,
        old_manual_review=old_review,
        new_enabled=new_enabled,
        new_scope=new_scope,
        new_mode=new_mode,
        new_manual_review=new_review,
        action=action,
    )


def plan(cur: sqlite3.Cursor) -> tuple[list[tuple[str, str]], list[PlannedUpdate], list[str]]:
    missing_cols = missing_policy_columns(cur)
    cols = table_columns(cur, "service_catalog")
    cols_present = all(name in cols for name, _ddl in POLICY_COLUMNS)

    updates = []
    for service_code in sorted(SEED_POLICY):
        updates.append(policy_diff(get_service(cur, service_code), service_code, cols_present))

    cur.execute("SELECT service_code FROM service_catalog ORDER BY service_code")
    all_codes = [str(row[0]) for row in cur.fetchall()]
    untouched = [c for c in all_codes if c not in SEED_POLICY]

    return missing_cols, updates, untouched


def apply_migration(cur: sqlite3.Cursor) -> tuple[int, int]:
    added_cols = 0
    for name, ddl in missing_policy_columns(cur):
        cur.execute(f"ALTER TABLE service_catalog ADD COLUMN {name} {ddl}")
        added_cols += 1

    updated = 0
    for service_code, policy in SEED_POLICY.items():
        row = get_service(cur, service_code)
        if not row:
            continue

        cur.execute("""
            UPDATE service_catalog
            SET
                access_policy_enabled = ?,
                access_policy_scope = ?,
                access_policy_mode = ?,
                access_policy_message = ?,
                manual_review_required = ?,
                policy_updated_at = ?,
                policy_updated_by = ?
            WHERE service_code = ?
        """, (
            int(policy["access_policy_enabled"]),
            str(policy["access_policy_scope"]),
            str(policy["access_policy_mode"]),
            policy["access_policy_message"],
            int(policy["manual_review_required"]),
            now_db(),
            "MIGRATE_service_catalog_v2_policy_fields.py",
            service_code,
        ))
        updated += cur.rowcount

    return added_cols, updated


def verify(cur: sqlite3.Cursor) -> list[str]:
    lines = []
    cols = table_columns(cur, "service_catalog")
    missing = [name for name, _ddl in POLICY_COLUMNS if name not in cols]
    lines.append(f"Policy columns missing: {len(missing)}")
    if missing:
        for c in missing:
            lines.append(f" - {c}")

    if not missing:
        cur.execute("""
            SELECT
                access_policy_mode,
                COUNT(*) AS n
            FROM service_catalog
            GROUP BY access_policy_mode
            ORDER BY access_policy_mode
        """)
        lines.append("")
        lines.append("Policy mode counts:")
        for row in cur.fetchall():
            lines.append(f" - {row['access_policy_mode']}: {row['n']}")

        cur.execute("""
            SELECT service_code, service_name, access_policy_enabled, access_policy_scope,
                   access_policy_mode, manual_review_required
            FROM service_catalog
            WHERE access_policy_enabled = 1 OR access_policy_mode <> 'NONE' OR manual_review_required = 1
            ORDER BY service_code
        """)
        lines.append("")
        lines.append("Services with active/warn/block policy:")
        for row in cur.fetchall():
            lines.append(
                f" - {row['service_code']} | mode={row['access_policy_mode']} | "
                f"scope={row['access_policy_scope']} | review={row['manual_review_required']} | {row['service_name']}"
            )

    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Explicit SQLite DB path. Required.")
    ap.add_argument("--apply", action="store_true", help="Actually migrate service_catalog.")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    print("=" * 88)
    print("OSBB service_catalog v2 policy fields migration")
    print("=" * 88)
    print("Mode:", "APPLY" if args.apply else "DRY RUN / READ ONLY")
    print("DB:", db_path)
    print("")

    readonly = not args.apply
    conn = connect(db_path, readonly=readonly)
    try:
        cur = conn.cursor()

        if not table_exists(cur, "service_catalog"):
            raise SystemExit("Table service_catalog not found.")

        missing_cols, updates, untouched = plan(cur)

        print("Missing policy columns:")
        if missing_cols:
            for name, ddl in missing_cols:
                print(f" - {name} {ddl}")
        else:
            print(" - none")
        print("")

        print("Planned seed policy updates:")
        for u in updates:
            print(
                f" - {u.service_code}: {u.action} | "
                f"{u.old_enabled}/{u.old_scope}/{u.old_mode}/review={u.old_manual_review} "
                f"-> {u.new_enabled}/{u.new_scope}/{u.new_mode}/review={u.new_manual_review}"
            )
        print("")

        if untouched:
            print("Service codes left unchanged by v1 seed:")
            for c in untouched:
                print(f" - {c}")
            print("")

        if not args.apply:
            print("VERIFY preview:")
            for line in verify(cur):
                print(line)
            print("")
            print("DRY RUN COMPLETED. Re-run with --apply to migrate.")
            return 0

        backup = backup_db(db_path)
        added, updated = apply_migration(cur)
        conn.commit()

        print("Backup:", backup)
        print("Columns added:", added)
        print("Rows updated:", updated)
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
