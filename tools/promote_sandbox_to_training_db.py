#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
promote_sandbox_to_training_db.py

Promote selected strongest sandbox DB to working training DB: osbb_test.db.

Safety:
  - DRY RUN by default
  - --apply required to copy
  - creates timestamped backup of current osbb_test.db
  - validates SQLite integrity before and after
  - writes promotion report
  - does NOT touch osbb.db

Default source:
  G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db

Default target:
  G:\Programming\Py\OSBB\Data\db\osbb_test.db
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


DEFAULT_SOURCE = r"G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db"
DEFAULT_TARGET = r"G:\Programming\Py\OSBB\Data\db\osbb_test.db"
DEFAULT_REPORT = r"G:\Programming\Py\OSBB\Data\exports\db_rank\PROMOTE_SANDBOX_TO_TRAINING_DB.md"

KEY_TABLES = [
    "apartments",
    "vehicles",
    "bot_admins",
    "operator_audit_log",
    "service_orders",
    "service_catalog",
    "service_items",
    "phone_access_requests",
    "phone_access_request_points",
    "cashbox_operations",
    "payments",
    "payment_allocations",
    "access_policy_versions",
    "unit_groups",
]


def connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def sqlite_ok(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"file not found: {path}"
    try:
        conn = connect_ro(path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                return False, f"integrity_check failed: {row}"
            return True, "ok"
        finally:
            conn.close()
    except Exception as e:
        return False, str(e)


def table_count(path: Path) -> int:
    conn = connect_ro(path)
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0])
    finally:
        conn.close()


def row_counts(path: Path) -> dict[str, int]:
    conn = connect_ro(path)
    try:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        out = {}
        for t in KEY_TABLES:
            if t in tables:
                try:
                    out[t] = int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
                except Exception:
                    out[t] = -1
        return out
    finally:
        conn.close()


def backup_target(target: Path) -> Path:
    backup_dir = target.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"{target.stem}_before_promote_live_services_{datetime.now():%Y-%m-%d_%H-%M-%S}{target.suffix}"
    shutil.copy2(target, dst)
    return dst


def write_report(report_path: Path, source: Path, target: Path, backup: Path | None, mode: str) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("# OSBB Training DB Promotion Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"Mode: **{mode}**")
    lines.append("")
    lines.append(f"Source: `{source}`")
    lines.append(f"Target: `{target}`")
    if backup:
        lines.append(f"Backup: `{backup}`")
    lines.append("")
    lines.append("## Source")
    lines.append("")
    lines.append(f"- tables: {table_count(source)}")
    lines.append(f"- size MB: {source.stat().st_size / 1024 / 1024:.2f}")
    lines.append("- key row counts:")
    for k, v in sorted(row_counts(source).items()):
        lines.append(f"  - `{k}`: {v}")
    lines.append("")
    if target.exists():
        lines.append("## Target")
        lines.append("")
        lines.append(f"- tables: {table_count(target)}")
        lines.append(f"- size MB: {target.stat().st_size / 1024 / 1024:.2f}")
        lines.append("- key row counts:")
        for k, v in sorted(row_counts(target).items()):
            lines.append(f"  - `{k}`: {v}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--report", default=DEFAULT_REPORT)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    source = Path(args.source)
    target = Path(args.target)
    report = Path(args.report)

    print("=" * 100)
    print("OSBB promote sandbox to training DB")
    print("=" * 100)
    print("Mode:", "APPLY" if args.apply else "DRY RUN")
    print("Source:", source)
    print("Target:", target)
    print("Report:", report)
    print("")

    ok, msg = sqlite_ok(source)
    print("Source integrity:", msg)
    if not ok:
        raise SystemExit(2)

    if target.exists():
        ok, msg = sqlite_ok(target)
        print("Current target integrity:", msg)
        if not ok:
            raise SystemExit(3)
    else:
        print("Current target: does not exist")

    print("")
    print("Source tables:", table_count(source))
    if target.exists():
        print("Target tables:", table_count(target))

    backup = None

    if not args.apply:
        write_report(report, source, target, backup, "DRY RUN")
        print("")
        print("DRY RUN COMPLETED. Re-run with --apply to promote.")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        backup = backup_target(target)
        print("Backup:", backup)

    shutil.copy2(source, target)

    ok, msg = sqlite_ok(target)
    print("New target integrity:", msg)
    if not ok:
        raise SystemExit(4)

    write_report(report, source, target, backup, "APPLY")

    print("")
    print("APPLY COMPLETED")
    print("New osbb_test.db:", target)
    print("Report:", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
