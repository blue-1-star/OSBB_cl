#!/usr/bin/env python3
"""Remove provably orphaned duplicate debug drafts from ``vehicles``.

This is a one-time *debug database cleanup*, not an operational deletion tool.
It only deletes an active DRAFT vehicle that:

* has no apartment;
* comes from ``cashier_or_registry_draft``;
* shares its normalized plate fragment with another qualifying draft; and
* has no reference from any known vehicle-linked business or audit table.

Run without arguments first.  It is a dry-run and changes nothing.
``--apply`` still requires typing the exact deletion count.  On apply, a
timestamped SQLite backup and JSON migration report are created before rows are
deleted in one transaction.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import paths, USE_TEST_DB


MIGRATION_ID = "2026-09-18_006_cleanup_debug_orphan_draft_vehicles"

# ``audit_log`` uses a generic record_id, therefore its scope must be narrowed
# to vehicle events.  The other references are explicit vehicle-id columns.
REFERENCE_SPECS: tuple[tuple[str, str, str | None], ...] = (
    ("adjustment_assignments", "vehicle_id", None),
    ("cashbox_operations", "vehicle_id", None),
    ("charges", "vehicle_id", None),
    ("operator_task_queue", "vehicle_id", None),
    ("parking_time_review_tasks", "vehicle_id", None),
    ("payments", "vehicle_id", None),
    ("resident_profile_change_requests", "vehicle_id", None),
    ("vehicle_candidates", "resolved_vehicle_id", None),
    ("vehicle_candidates", "merged_vehicle_id", None),
    ("audit_log", "record_id", "table_name = 'vehicles'"),
)


def get_db_path() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})")}


def reference_counts(conn: sqlite3.Connection, vehicle_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, column, condition in REFERENCE_SPECS:
        if column not in table_columns(conn, table):
            continue
        sql = (
            f"SELECT COUNT(*) FROM {quote_identifier(table)} "
            f"WHERE {quote_identifier(column)} = ?"
        )
        if condition:
            sql += f" AND {condition}"
        count = int(conn.execute(sql, (vehicle_id,)).fetchone()[0])
        if count:
            counts[f"{table}.{column}"] = count
    return counts


def duplicate_draft_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        WITH duplicate_fragments AS (
            SELECT COALESCE(license_plate_normalized, license_plate, '') AS plate
              FROM vehicles
             WHERE apartment_id IS NULL
               AND COALESCE(status, '') = 'DRAFT'
               AND COALESCE(source, '') = 'cashier_or_registry_draft'
               AND COALESCE(lifecycle_status, 'ACTIVE') = 'ACTIVE'
             GROUP BY COALESCE(license_plate_normalized, license_plate, '')
            HAVING COUNT(*) > 1
        )
        SELECT v.id,
               COALESCE(v.license_plate_normalized, v.license_plate, '') AS plate,
               v.parking_time,
               v.created_at,
               v.created_by,
               v.notes
          FROM vehicles v
          JOIN duplicate_fragments d
            ON d.plate = COALESCE(v.license_plate_normalized, v.license_plate, '')
         WHERE v.apartment_id IS NULL
           AND COALESCE(v.status, '') = 'DRAFT'
           AND COALESCE(v.source, '') = 'cashier_or_registry_draft'
           AND COALESCE(v.lifecycle_status, 'ACTIVE') = 'ACTIVE'
         ORDER BY plate, v.id
        """
    ).fetchall()


def classify(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    deletable: list[dict] = []
    protected: list[dict] = []
    for row in duplicate_draft_rows(conn):
        item = dict(row)
        references = reference_counts(conn, int(row["id"]))
        item["references"] = references
        (protected if references else deletable).append(item)
    return deletable, protected


def print_plan(deletable: list[dict], protected: list[dict]) -> None:
    print(f"\n{MIGRATION_ID}")
    print("Candidates to DELETE (no references):")
    if not deletable:
        print("  — none")
    for item in deletable:
        print(
            f"  id={item['id']} | plate={item['plate']!r} | "
            f"parking_time={item['parking_time']!r} | created_at={item['created_at']}"
        )

    print("\nProtected duplicate drafts (kept because they have references):")
    if not protected:
        print("  — none")
    for item in protected:
        links = ", ".join(f"{name}={count}" for name, count in item["references"].items())
        print(f"  id={item['id']} | plate={item['plate']!r} | {links}")

    print(f"\nSummary: delete={len(deletable)}, keep={len(protected)}")


def backup_database(db_path: Path, backup_path: Path) -> None:
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def write_report(report_path: Path, payload: dict) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the deletion after confirmation")
    args = parser.parse_args()

    db_path = get_db_path()
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}")
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        deletable, protected = classify(conn)
    finally:
        conn.close()

    print(f"Database: {db_path}")
    print_plan(deletable, protected)
    if not args.apply:
        print("\nDRY-RUN: no database changes. Re-run with --apply to continue.")
        return 0
    if not deletable:
        print("\nNothing to delete.")
        return 0

    expected = f"DELETE {len(deletable)}"
    answer = input(f"\nType exactly {expected!r} to apply this cleanup: ").strip()
    if answer != expected:
        print("Cancelled: no database changes.")
        return 1

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = paths.BACKUPS_DIR
    report_dir = paths.LOGS_DIR / "migrations"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{MIGRATION_ID}_{timestamp}.db"
    report_path = report_dir / f"{MIGRATION_ID}_{timestamp}.json"
    backup_database(db_path, backup_path)

    ids = [int(item["id"]) for item in deletable]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ", ".join("?" for _ in ids)
        conn.execute(f"DELETE FROM vehicles WHERE id IN ({placeholders})", ids)
        remaining_deletable, remaining_protected = classify(conn)
        if remaining_deletable:
            raise RuntimeError("verification failed: deletable duplicates remain")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    write_report(
        report_path,
        {
            "migration": MIGRATION_ID,
            "executed_at": timestamp,
            "database": str(db_path),
            "backup": str(backup_path),
            "deleted_vehicle_ids": ids,
            "protected_duplicate_drafts": protected,
            "remaining_protected_duplicate_drafts": remaining_protected,
        },
    )
    print(f"\nDeleted vehicle IDs: {ids}")
    print(f"Backup: {backup_path}")
    print(f"Migration report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
