#!/usr/bin/env python3
"""Add provenance for service requests copied from messages by an operator."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from service_interest_intake_core import ensure_intake_schema

DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUPS = ROOT / "Data" / "db" / "backups"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.is_file():
        raise SystemExit(f"DB not found: {db}")
    print("DB:", db)
    if not args.apply:
        print("DRY RUN ONLY — no changes saved.")
        return 0
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"before_2026-09-23_023_operator_service_interest_intake_{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(db) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    with sqlite3.connect(db) as conn:
        ensure_intake_schema(conn)
        conn.commit()
    print("APPLIED. Backup:", backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
